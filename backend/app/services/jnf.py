"""Company Job Notification Forms (JNF): the company fills the form through a signed link; the cell approves it
into a drive.

What leaves this module for the company is the form itself and aggregate counts, never a student's details.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.security import create_jnf_token, create_link_token
from ..core.timeutil import ensure_utc, utcnow
from ..models import (
    Company,
    Drive,
    DriveStatus,
    EligibilityRule,
    JobNotification,
    Round,
    RoundKind,
    Student,
)
from ..schemas import JnfCriteria, JnfForm
from . import jd_parser
from .eligibility import RuleSpec, preview_pool, recompute_drive
from .events import emit
from .insights import pool_placements
from .notifications import announce_drive
from .policy import active_policy

EDITABLE = ("invited", "submitted", "changes_requested")
VARIABLE_FLAG = 0.25  # variable pay at or above this share of the CTC gets a flag


class JnfError(Exception):
    pass


# ---------------------------------------------------------------- links and views
def link_url(n: JobNotification) -> str:
    return f"{get_settings().public_url}/j/{create_jnf_token(n.id, n.link_version)}"


def recruiter_url(d: Drive) -> str:
    return f"{get_settings().public_url}/r/{create_link_token('recruiter', d.id, d.link_version)}"


def _iso(dt: datetime | None) -> str | None:
    return ensure_utc(dt).isoformat() if dt else None  # type: ignore[union-attr]


def row(n: JobNotification) -> dict[str, Any]:
    data = n.data or {}
    return {"id": n.id, "company_name": n.company_name, "contact_name": n.contact_name,
            "contact_email": n.contact_email, "status": n.status, "role": data.get("role"),
            "ctc_lpa": data.get("ctc_lpa"), "drive_id": n.drive_id, "cell_note": n.cell_note,
            "invited_by": n.invited_by, "decided_by": n.decided_by, "created_at": _iso(n.created_at),
            "submitted_at": _iso(n.submitted_at), "decided_at": _iso(n.decided_at)}


def options(db: Session) -> dict[str, list[str]]:
    """Branches and programmes that exist in the student body, for the form's checkboxes."""
    pairs = db.execute(select(Student.branch, Student.programme).where(Student.archived.is_(False)).distinct()).all()
    return {"branches": sorted({b for b, _ in pairs if b}), "programmes": sorted({p for _, p in pairs if p})}


def public_view(db: Session, n: JobNotification) -> dict[str, Any]:
    return {"id": n.id, "status": n.status, "company_name": n.company_name, "contact_name": n.contact_name,
            "data": n.data or None,
            "cell_note": n.cell_note if n.status in ("changes_requested", "declined") else None,
            "editable": n.status in EDITABLE, "options": options(db)}


# ---------------------------------------------------------------- criteria and the live count
def criteria_rules(c: JnfCriteria) -> list[RuleSpec]:
    rules: list[RuleSpec] = []
    if c.cgpa_min is not None:
        rules.append(RuleSpec("cgpa", ">=", c.cgpa_min))
    if c.max_backlogs is not None:
        rules.append(RuleSpec("backlogs", "<=", c.max_backlogs))
    if c.branches:
        rules.append(RuleSpec("branch", "in", c.branches))
    if c.programmes:
        rules.append(RuleSpec("programme", "in", c.programmes))
    return rules


def pool_count(db: Session, c: JnfCriteria) -> dict[str, Any]:
    """How many students these criteria admit, with the tier the policy gives the CTC. Aggregates only."""
    profiles, placements = pool_placements(db)
    _, cfg = active_policy(db)
    eligible = preview_pool(profiles, criteria_rules(c), cfg, placements, c.ctc_lpa)
    return {"eligible": eligible, "total_students": len(profiles), "tier": cfg.tier_name(c.ctc_lpa)}


# ---------------------------------------------------------------- local dates (IST)
def _zone() -> timezone:
    return timezone(timedelta(minutes=get_settings().display_utc_offset_min))


def _local_date(dt: datetime) -> date:
    return ensure_utc(dt).astimezone(_zone()).date()  # type: ignore[union-attr]


def _at_local(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=_zone()).astimezone(UTC)


def _day(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


# ---------------------------------------------------------------- the cell's review
def review(db: Session, n: JobNotification) -> dict[str, Any]:
    form = JnfForm.model_validate(n.data)
    pool = pool_count(db, form)
    ctc, flags = form.ctc_lpa, []
    if form.bond_months:
        flags.append(f"Bond of {form.bond_months} months" + (f": {form.bond_note}" if form.bond_note else ""))
    if form.variable_lpa and form.variable_lpa / ctc >= VARIABLE_FLAG:
        flags.append(f"Variable pay is {round(100 * form.variable_lpa / ctc)}% of the CTC")
    parts = [x for x in (form.base_lpa, form.variable_lpa, form.joining_bonus_lpa, form.other_lpa) if x is not None]
    if parts and abs(sum(parts) - ctc) > max(0.5, 0.05 * ctc):
        flags.append(f"The breakup adds up to {sum(parts):g} LPA, not {ctc:g} LPA")
    if form.preferred_date:
        others = db.scalars(select(Drive).where(Drive.drive_start.is_not(None), Drive.status != DriveStatus.cancelled))
        for d in others:
            if d.id != n.drive_id and d.drive_start and _local_date(d.drive_start) == form.preferred_date:
                flags.append(f"{d.company.name} also has a drive on {_day(form.preferred_date)}")
    eligible, hires = pool["eligible"], form.target_hires
    if eligible == 0:
        flags.append("No student meets these criteria")
    elif eligible < 2 * hires:
        flags.append(f"Only {eligible} eligible student{'s' if eligible != 1 else ''} for {hires} "
                     f"hire{'s' if hires != 1 else ''}")
    return {**pool, "flags": flags}


# ---------------------------------------------------------------- state changes
def submit(db: Session, n: JobNotification, form: JnfForm, actor: str) -> None:
    if n.status not in EDITABLE:
        raise JnfError(f"This form was already {n.status}. Contact the placement cell to change it.")
    resubmitted = n.status != "invited"
    n.data = form.model_dump(mode="json")
    n.status = "submitted"
    n.submitted_at = utcnow()
    emit(db, "jnf.submitted", actor, entity="jnf", entity_id=n.id, payload={"resubmitted": resubmitted})


def _company_for(db: Session, n: JobNotification, actor: str) -> Company:
    company = db.get(Company, n.company_id) if n.company_id else None
    if company is None:
        company = db.scalar(select(Company).where(func.lower(Company.name) == n.company_name.strip().lower()))
    if company is not None:
        if company.archived:
            raise JnfError(f"{company.name} is archived. Restore it on Companies before approving this form.")
        return company
    company = Company(name=n.company_name.strip(), contact_name=n.contact_name, contact_email=n.contact_email)
    db.add(company)
    db.flush()
    emit(db, "company.created", actor, entity="company", entity_id=company.id)
    return company


def approve(db: Session, n: JobNotification, actor: str, open_now: bool) -> tuple[Drive, bool]:
    """Turn a submitted form into a drive. It opens only when no eligibility rule is left to approve."""
    if n.status != "submitted":
        raise JnfError("Only a submitted form can be approved." if n.status in EDITABLE
                       else f"This form was already {n.status}.")
    form = JnfForm.model_validate(n.data)
    company = _company_for(db, n, actor)
    d = Drive(company_id=company.id, role=form.role, jd_text=form.jd_text, ctc_lpa=form.ctc_lpa,
              location=form.location, target_hires=form.target_hires, status=DriveStatus.draft,
              apply_deadline=_at_local(form.apply_by, 23, 59) if form.apply_by else None,
              drive_start=_at_local(form.preferred_date, 9, 0) if form.preferred_date else None)
    source = f"Job notification form from {company.name}"
    for spec in criteria_rules(form):
        d.rules.append(EligibilityRule(field=spec.field, op=spec.op, value=spec.value, source_text=source,
                                       confidence=1.0, approved=True, approved_by=actor))
    covered = {r.field for r in d.rules}
    if form.jd_text.strip():
        ext = jd_parser.extract(form.jd_text, get_settings())
        d.skills = ext.skills
        for r in ext.rules:  # the form's own criteria win; the JD only adds what the form didn't cover
            if r.field not in covered:
                d.rules.append(EligibilityRule(field=r.field, op=r.op, value=r.value, source_text=r.source_text,
                                               confidence=r.confidence, approved=False))
                covered.add(r.field)
    for seq, r in enumerate(form.rounds, start=1):
        d.rounds.append(Round(name=r.name, kind=RoundKind(r.kind), seq=seq, duration_min=r.duration_min))
    db.add(d)
    db.flush()
    emit(db, "drive.created", actor, entity="drive", entity_id=d.id, drive_id=d.id, payload={"jnf": n.id})
    recompute_drive(db, d)
    opened = open_now and all(r.approved for r in d.rules)
    if opened:
        d.status = DriveStatus.open
        announce_drive(db, d)
        emit(db, "drive.updated", actor, entity="drive", entity_id=d.id, drive_id=d.id, payload={"fields": ["status"]})
    n.status, n.drive_id, n.company_id = "approved", d.id, company.id
    n.decided_by, n.decided_at = actor, utcnow()
    emit(db, "jnf.approved", actor, entity="jnf", entity_id=n.id, drive_id=d.id, payload={"opened": opened})
    return d, opened


def request_changes(db: Session, n: JobNotification, note: str, actor: str) -> None:
    if n.status != "submitted":
        raise JnfError("Only a submitted form can be sent back for changes.")
    n.status, n.cell_note = "changes_requested", note
    emit(db, "jnf.changes_requested", actor, entity="jnf", entity_id=n.id)


def decline(db: Session, n: JobNotification, note: str, actor: str) -> None:
    if n.status not in EDITABLE:
        raise JnfError(f"This form was already {n.status}.")
    n.status, n.cell_note = "declined", note
    n.decided_by, n.decided_at = actor, utcnow()
    emit(db, "jnf.declined", actor, entity="jnf", entity_id=n.id)


def rotate_link(db: Session, n: JobNotification, actor: str) -> None:
    n.link_version += 1
    emit(db, "jnf.link_rotated", actor, entity="jnf", entity_id=n.id)
