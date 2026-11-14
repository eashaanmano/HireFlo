"""Explained eligibility: approved JD rules + institute policy -> a decision with a reason for every check."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..core.timeutil import utcnow
from ..models import Drive, EligibilityDecision, EligibilityRule, Student
from .policy import Placement, PolicyConfig, active_policy, check_policy, placements_for_all

FIELD_LABELS: dict[str, str] = {
    "cgpa": "CGPA",
    "backlogs": "Active backlogs",
    "branch": "Branch",
    "programme": "Programme",
    "grad_year": "Graduation year",
}


@dataclass(frozen=True)
class StudentProfile:
    id: int
    cgpa: float
    backlogs: int
    branch: str
    programme: str
    grad_year: int

    @classmethod
    def of(cls, s: Student) -> StudentProfile:
        return cls(s.id, s.cgpa, s.backlogs, s.branch, s.programme, s.grad_year)


@dataclass(frozen=True)
class RuleSpec:
    field: str
    op: str
    value: Any
    id: int | None = None


@dataclass
class Reason:
    code: str           # rule:<id> or policy code P1..P4
    ok: bool
    message: str
    field: str | None = None
    fixable: bool = False   # e.g. backlogs can be cleared; CGPA and branch cannot change mid-season


@dataclass
class Decision:
    eligible: bool
    reasons: list[Reason] = field(default_factory=list)

    @property
    def failed(self) -> list[Reason]:
        return [r for r in self.reasons if not r.ok]

    def as_json(self) -> list[dict[str, Any]]:
        return [asdict(r) for r in self.reasons]


def _fmt(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def check_rule(profile: StudentProfile, rule: RuleSpec) -> Reason:
    actual: Any = getattr(profile, rule.field)
    label = FIELD_LABELS.get(rule.field, rule.field)
    code = f"rule:{rule.id}" if rule.id is not None else f"rule:{rule.field}"
    if rule.op == ">=":
        ok = float(actual) >= float(rule.value)
        msg = f"{label} {_fmt(actual)} {'meets' if ok else 'is below'} the required {_fmt(rule.value)}"
    elif rule.op == "<=":
        ok = float(actual) <= float(rule.value)
        limit = int(rule.value) if rule.field == "backlogs" else rule.value
        msg = f"{label}: {_fmt(actual)} (allowed: at most {_fmt(limit)})"
    elif rule.op == "in":
        allowed = [str(v).upper() for v in (rule.value or [])]
        ok = str(actual).upper() in allowed
        msg = f"{label} {actual} {'is' if ok else 'is not'} in the allowed list ({_fmt(rule.value)})"
    elif rule.op == "==":
        ok = str(actual) == str(rule.value)
        msg = f"{label} {actual} {'matches' if ok else 'does not match'} {rule.value}"
    else:  # pragma: no cover - guarded by schema validation
        raise ValueError(f"unsupported operator {rule.op}")
    return Reason(code=code, ok=ok, message=msg, field=rule.field, fixable=rule.field == "backlogs")


def evaluate(profile: StudentProfile, rules: Iterable[RuleSpec], cfg: PolicyConfig,
             placement: Placement, drive_ctc: float, drive_id: int | None = None) -> Decision:
    reasons = [check_rule(profile, r) for r in rules]
    for pc in check_policy(cfg, placement, drive_ctc, drive_id):
        reasons.append(Reason(code=pc.code, ok=pc.ok, message=pc.message, field=None))
    return Decision(eligible=all(r.ok for r in reasons), reasons=reasons)


def rule_specs(rules: Sequence[EligibilityRule], approved_only: bool = True) -> list[RuleSpec]:
    return [RuleSpec(r.field, r.op, r.value, r.id) for r in rules if r.approved or not approved_only]


def recompute_drive(db: Session, drive: Drive) -> dict[str, int]:
    """Recompute and store decisions for every active student. Returns eligible/total counts."""
    _, cfg = active_policy(db)
    placements = placements_for_all(db, cfg)
    specs = rule_specs(drive.rules)
    students = db.scalars(select(Student).where(Student.archived.is_(False))).all()
    db.execute(delete(EligibilityDecision).where(EligibilityDecision.drive_id == drive.id))
    now = utcnow()
    eligible = 0
    for s in students:
        decision = evaluate(StudentProfile.of(s), specs, cfg, placements.get(s.id, Placement()), drive.ctc_lpa,
                            drive.id)
        eligible += decision.eligible
        db.add(EligibilityDecision(drive_id=drive.id, student_id=s.id, eligible=decision.eligible,
                                   reasons=decision.as_json(), computed_at=now))
    db.flush()
    return {"eligible": eligible, "total": len(students)}


def recompute_for_student(db: Session, student: Student, drives: Iterable[Drive]) -> None:
    """Used after an offer is accepted: the policy lock changes this student's eligibility everywhere."""
    _, cfg = active_policy(db)
    placements = placements_for_all(db, cfg, student_id=student.id)
    profile = StudentProfile.of(student)
    now = utcnow()
    for drive in drives:
        decision = evaluate(profile, rule_specs(drive.rules), cfg, placements.get(student.id, Placement()),
                            drive.ctc_lpa, drive.id)
        row = db.get(EligibilityDecision, (drive.id, student.id))
        if row is None:
            db.add(EligibilityDecision(drive_id=drive.id, student_id=student.id, eligible=decision.eligible,
                                       reasons=decision.as_json(), computed_at=now))
        else:
            row.eligible, row.reasons, row.computed_at = decision.eligible, decision.as_json(), now
    db.flush()


def preview_pool(profiles: Sequence[StudentProfile], rules: Sequence[RuleSpec], cfg: PolicyConfig,
                 placements: dict[int, Placement], drive_ctc: float, drive_id: int | None = None) -> int:
    """Count eligible students for a hypothetical rule set (recruiter what-if)."""
    return sum(
        evaluate(p, rules, cfg, placements.get(p.id, Placement()), drive_ctc, drive_id).eligible for p in profiles
    )
