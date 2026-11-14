"""Company insight profiles (SRS FUT-3, FUT-4).

A profile is built only from a company's completed drives at the institute and contains aggregates only: roles,
CTC range, the round structure, branches hired and how many candidates passed each stage. No individual student
appears in it, so it can be shown to students before they apply.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.timeutil import ensure_utc
from ..models import AppStatus, Company, Drive, DriveStatus
from .insights import application_funnel
from .policy import active_policy


def season_of(dt: datetime | None) -> str | None:
    """Academic placement season label: July 2026 to June 2027 is '2026-27'."""
    if dt is None:
        return None
    dt = ensure_utc(dt)
    assert dt is not None
    start = dt.year if dt.month >= 7 else dt.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def drive_season(d: Drive) -> str | None:
    return season_of(d.drive_start or d.apply_deadline or d.created_at)


def _rate(num: int, den: int) -> float | None:
    return round(100 * num / den, 1) if den else None


def company_profile(db: Session, company: Company, exclude_drive_id: int | None = None) -> dict[str, Any]:
    _, cfg = active_policy(db)
    drives = [d for d in db.scalars(select(Drive).where(Drive.company_id == company.id,
                                                       Drive.status == DriveStatus.completed).order_by(Drive.id))
              if d.id != exclude_drive_id]
    base = {"company": {"id": company.id, "name": company.name, "sector": company.sector}, "has_history": bool(drives)}
    if not drives:
        return base | {"seasons": [], "drives": 0, "roles": [], "ctc": None, "tiers": [], "rounds": [],
                       "branches_hired": [], "stages": [], "selection_rate": None}

    apps = [a for d in drives for a in d.applications if a.status != AppStatus.withdrawn]
    f = application_funnel(apps)
    selected = [a for a in apps if a.status == AppStatus.selected]
    hired = Counter(a.student.branch for a in selected)
    latest = drives[-1]
    ctcs = [d.ctc_lpa for d in drives]
    stages = [
        {"stage": "Applied", "count": f.applied, "rate": 100.0 if f.applied else None},
        {"stage": "Shortlisted", "count": f.shortlisted, "rate": _rate(f.shortlisted, f.applied)},
        {"stage": "Cleared online test", "count": f.test_cleared, "rate": _rate(f.test_cleared, f.test_reached)},
        {"stage": "Cleared interviews", "count": f.interview_cleared,
         "rate": _rate(f.interview_cleared, f.interview_reached)},
        {"stage": "Offer", "count": f.offers, "rate": _rate(f.offers, f.applied)},
    ]
    return base | {
        "seasons": sorted({s for s in (drive_season(d) for d in drives) if s}),
        "drives": len(drives),
        "roles": sorted({d.role for d in drives}),
        "ctc": {"min": min(ctcs), "max": max(ctcs)},
        "tiers": sorted({cfg.tier_name(c) for c in ctcs}),
        "rounds": [{"name": r.name, "kind": r.kind.value, "duration_min": r.duration_min} for r in latest.rounds],
        "branches_hired": [{"branch": b, "selected": n} for b, n in hired.most_common()],
        "stages": stages,
        "selection_rate": _rate(len(selected), len(apps)),
    }
