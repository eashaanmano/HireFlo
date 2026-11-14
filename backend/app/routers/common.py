"""Shared helpers for routers: lookups that 404 cleanly and model -> response conversions."""
from __future__ import annotations

from typing import TypeVar

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.database import Base
from ..models import Application, Drive, EligibilityDecision, Slot
from ..schemas import ApplicationOut, DriveOut, OfferOut, SlotOut
from ..services.exports import stage_label
from ..services.live import SlotProjection
from ..services.policy import PolicyConfig

T = TypeVar("T", bound=Base)


def get_or_404(db: Session, model: type[T], obj_id: int, what: str | None = None) -> T:
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{what or model.__name__} not found.")
    return obj


def bad_request(msg: str) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, msg)


def conflict(msg: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, msg)


def drive_out(db: Session, d: Drive, cfg: PolicyConfig, with_counts: bool = True) -> DriveOut:
    eligible = None
    if with_counts:
        eligible = db.scalar(select(func.count()).select_from(EligibilityDecision).where(
            EligibilityDecision.drive_id == d.id, EligibilityDecision.eligible.is_(True)))
    return DriveOut(
        id=d.id, company_id=d.company_id, company=d.company.name, role=d.role, jd_text=d.jd_text,
        ctc_lpa=d.ctc_lpa, tier=cfg.tier_name(d.ctc_lpa), sector=d.company.sector, location=d.location,
        status=d.status,
        apply_deadline=d.apply_deadline, drive_start=d.drive_start, target_hires=d.target_hires,
        skills=d.skills or [], applicants=len([a for a in d.applications if a.status.value != "withdrawn"]),
        eligible=eligible, rules_pending=sum(1 for r in d.rules if not r.approved),
    )


def skill_match(student_skills: list[str], drive_skills: list[str]) -> float | None:
    if not drive_skills:
        return None
    have = {s.lower() for s in student_skills}
    return round(sum(1 for s in drive_skills if s.lower() in have) / len(drive_skills), 2)


def application_out(a: Application) -> ApplicationOut:
    s = a.student
    return ApplicationOut(
        id=a.id, drive_id=a.drive_id, student_id=s.id, student=s.name, roll_no=s.roll_no, branch=s.branch,
        cgpa=s.cgpa, status=a.status, stage=stage_label(a), rounds_cleared=a.rounds_cleared,
        skill_match=skill_match(s.skills, a.drive.skills or []),
        offer_status=a.offer.status if a.offer else None,
    )


def slot_out(s: Slot, proj: SlotProjection | None = None) -> SlotOut:
    st = s.application.student
    return SlotOut(
        id=s.id, round=s.round.name, round_id=s.round_id, panel=s.panel.name, panel_id=s.panel_id,
        room=s.panel.room, student=st.name, student_id=st.id, roll_no=st.roll_no, status=s.status,
        start_at=s.start_at, end_at=s.end_at, projected_start=proj.projected_start if proj else None,
        lateness_min=proj.lateness_min if proj else 0, checked_in=s.checked_in_at is not None, result=s.result,
    )


def offer_out(a: Application, cfg: PolicyConfig) -> OfferOut:
    assert a.offer is not None
    return OfferOut(id=a.offer.id, application_id=a.id, company=a.drive.company.name, role=a.drive.role,
                    ctc_lpa=a.offer.ctc_lpa, tier=cfg.tier_name(a.offer.ctc_lpa), deadline=a.offer.deadline,
                    status=a.offer.status)


def fresh_eligible_counts(db: Session, drive_ids: list[int]) -> dict[int, int]:
    if not drive_ids:
        return {}
    rows = db.execute(select(EligibilityDecision.drive_id, func.count()).where(
        EligibilityDecision.drive_id.in_(drive_ids), EligibilityDecision.eligible.is_(True))
        .group_by(EligibilityDecision.drive_id)).all()
    return {d: n for d, n in rows}
