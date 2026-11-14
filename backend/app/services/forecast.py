"""Placement forecasting and early warning (SRS FUT-1, FUT-2).

Forecast, per branch: students already placed plus, for every unplaced student, the chance of at least one offer
from the upcoming drives they are eligible for. A drive's chance is its tier's selection rate on completed drives
(students selected / students who applied), so the forecast is driven by the institute's own history and is shown
together with that basis.

Early warning lists unplaced students whose pattern makes them likely to stay unplaced, each with the reasons and a
suggested intervention. It is placement-cell only (never exposed to recruiter or student links).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AppStatus, Drive, DriveStatus, EligibilityDecision, Student
from .insights import application_funnel
from .offer_sim import DEFAULT_BASE_RATE, tier_base_rates
from .policy import active_policy, placements_for_all

UPCOMING = (DriveStatus.draft, DriveStatus.open, DriveStatus.closed, DriveStatus.live)
FINISHED = (DriveStatus.closed, DriveStatus.live, DriveStatus.completed)
MAX_CHANCE = 0.95
LOW_CHANCE = 0.30


@dataclass
class StudentOutlook:
    student: Student
    placed: bool
    chance: float                       # chance of at least one offer from upcoming eligible drives
    eligible_upcoming: int
    reasons: list[str] = field(default_factory=list)


def _outlooks(db: Session) -> tuple[list[StudentOutlook], dict[str, float], int]:
    _, cfg = active_policy(db)
    rates = tier_base_rates(db, cfg)
    placements = placements_for_all(db)
    upcoming = {d.id: d for d in db.scalars(select(Drive).where(Drive.status.in_(UPCOMING)))}
    eligible: dict[int, list[Drive]] = defaultdict(list)
    if upcoming:
        for sid, did in db.execute(select(EligibilityDecision.student_id, EligibilityDecision.drive_id).where(
                EligibilityDecision.drive_id.in_(list(upcoming)), EligibilityDecision.eligible.is_(True))):
            eligible[sid].append(upcoming[did])
    out: list[StudentOutlook] = []
    for s in db.scalars(select(Student).where(Student.archived.is_(False)).order_by(Student.roll_no)):
        if s.id in placements:
            out.append(StudentOutlook(s, True, 1.0, len(eligible[s.id])))
            continue
        miss = 1.0
        for d in eligible[s.id]:
            miss *= 1.0 - rates.get(cfg.tier_name(d.ctc_lpa), DEFAULT_BASE_RATE)
        out.append(StudentOutlook(s, False, round(min(MAX_CHANCE, 1.0 - miss), 3), len(eligible[s.id])))
    return out, rates, len(upcoming)


def branch_forecast(db: Session) -> dict[str, Any]:
    """FUT-1: expected placements per branch by the end of the season, with its basis."""
    outlooks, rates, n_upcoming = _outlooks(db)
    by: dict[str, dict[str, float]] = defaultdict(lambda: {"students": 0, "placed": 0, "expected": 0.0})
    for o in outlooks:
        b = by[o.student.branch]
        b["students"] += 1
        b["placed"] += o.placed
        b["expected"] += 1.0 if o.placed else o.chance
    branches = []
    for name, b in sorted(by.items(), key=lambda kv: -kv[1]["students"]):
        forecast = round(b["expected"])
        branches.append({
            "branch": name, "students": int(b["students"]), "placed": int(b["placed"]),
            "forecast_placed": forecast,
            "forecast_pct": round(100 * b["expected"] / b["students"], 1) if b["students"] else 0.0,
            "placed_pct": round(100 * b["placed"] / b["students"], 1) if b["students"] else 0.0,
        })
    total = sum(b["students"] for b in branches)
    placed = sum(b["placed"] for b in branches)
    expected = sum(by[b["branch"]]["expected"] for b in branches)
    return {
        "branches": branches,
        "totals": {"students": total, "placed": placed, "forecast_placed": round(expected),
                   "forecast_pct": round(100 * expected / total, 1) if total else 0.0},
        "basis": {
            "tier_rates": rates, "default_rate": DEFAULT_BASE_RATE, "upcoming_drives": n_upcoming,
            "method": "Placed students count as placed. For each unplaced student, the chance of at least one offer is "
                      "1 minus the product of (1 minus the tier selection rate) over the upcoming drives they are "
                      "eligible for. Tier rates come from completed drives; tiers with too little history use the "
                      "default rate.",
        },
    }


def early_warning(db: Session) -> list[dict[str, Any]]:
    """FUT-2: unplaced students likely to stay unplaced, with reasons and a suggested intervention."""
    outlooks, _rates, _n = _outlooks(db)
    flagged = []
    for o in outlooks:
        if o.placed:
            continue
        s = o.student
        apps = [a for a in s.applications if a.status != AppStatus.withdrawn]
        f = application_funnel(apps)
        interview_losses = f.interview_reached - f.interview_cleared
        test_losses = f.test_reached - f.test_cleared
        missed = db.scalar(
            select(func.count()).select_from(EligibilityDecision).join(Drive, Drive.id == EligibilityDecision.drive_id)
            .where(EligibilityDecision.student_id == s.id, EligibilityDecision.eligible.is_(True),
                   Drive.status.in_(FINISHED))) or 0
        reasons: list[str] = []
        suggestion = None
        if o.eligible_upcoming == 0:
            reasons.append("No eligible upcoming drive")
            suggestion = suggestion or ("Clear backlogs" if s.backlogs else "Bring in companies for this profile")
        if interview_losses >= 2:
            reasons.append(f"Dropped at the interview stage {interview_losses} times")
            suggestion = suggestion or "Mock interviews"
        if test_losses >= 3:
            reasons.append(f"Dropped at the online test {test_losses} times")
            suggestion = suggestion or "Aptitude test practice"
        if not apps and missed >= 3:
            reasons.append(f"Has not applied to any of {missed} eligible drives")
            suggestion = suggestion or "Counselling check-in"
        if o.eligible_upcoming and o.chance < LOW_CHANCE:
            reasons.append(f"Low chance of an offer from upcoming drives ({round(100 * o.chance)}%)")
            suggestion = suggestion or "Apply more widely; resume review"
        if not reasons:
            continue
        severity = "high" if (o.eligible_upcoming == 0 or len(reasons) >= 2) else "medium"
        flagged.append({
            "id": s.id, "name": s.name, "roll_no": s.roll_no, "branch": s.branch, "cgpa": s.cgpa,
            "backlogs": s.backlogs, "chance": o.chance, "eligible_upcoming": o.eligible_upcoming,
            "reasons": reasons, "suggestion": suggestion, "severity": severity,
        })
    flagged.sort(key=lambda r: (r["severity"] != "high", r["chance"], r["branch"]))
    return flagged
