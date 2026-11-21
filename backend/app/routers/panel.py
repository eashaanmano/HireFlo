"""Interview panel view behind a panel-scoped link. One tap on 'Done' moves the whole drive forward."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from ..core.deps import DbSession, PanelCtx
from ..core.ratelimit import rate_limit
from ..core.timeutil import utcnow
from ..models import Resume, Slot, SlotStatus
from ..schemas import SlotOut, SlotResult
from ..services import live
from .common import bad_request, get_or_404, resume_response, slot_out

router = APIRouter(prefix="/api/p", tags=["panel link"], dependencies=[Depends(rate_limit("panel", 240, 60))])


def _slot(db: DbSession, ctx: PanelCtx, slot_id: int) -> Slot:
    s = get_or_404(db, Slot, slot_id, "Slot")
    if s.panel_id != ctx.panel.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Slot not found.")
    return s


@router.get("/queue")
def queue(ctx: PanelCtx, db: DbSession) -> dict[str, Any]:
    rnd = ctx.panel.round
    slots = db.scalars(select(Slot).where(Slot.panel_id == ctx.panel.id)).all()
    proj = live.project_panel(slots, rnd.duration_min, utcnow())
    by_id = {sp.slot.id: sp for sp in proj}
    current = next((sp for sp in proj if sp.slot.status == SlotStatus.in_progress), None)
    waiting = [sp for sp in proj if sp.slot.status == SlotStatus.scheduled]
    done = [s for s in slots if s.status in (SlotStatus.done, SlotStatus.no_show)]

    def card(sp_slot: Slot) -> dict[str, Any]:
        st = sp_slot.application.student
        base = slot_out(sp_slot, by_id.get(sp_slot.id)).model_dump(mode="json")
        base.update({"cgpa": st.cgpa, "branch": st.branch, "programme": st.programme, "skills": st.skills,
                     "has_resume": bool(st.resumes), "application_id": sp_slot.application_id})
        return base

    return {
        "panel": {"id": ctx.panel.id, "name": ctx.panel.name, "room": ctx.panel.room,
                  "interviewer": ctx.panel.interviewer, "round": rnd.name, "duration_min": rnd.duration_min},
        "drive": {"company": ctx.drive.company.name, "role": ctx.drive.role, "skills": ctx.drive.skills},
        "current": card(current.slot) if current else None,
        "waiting": [card(sp.slot) for sp in waiting],
        "done": [slot_out(s).model_dump(mode="json") for s in sorted(done, key=lambda s: s.start_at)],
    }


@router.post("/slots/{slot_id}/start", response_model=SlotOut)
def start(slot_id: int, ctx: PanelCtx, db: DbSession) -> SlotOut:
    s = _slot(db, ctx, slot_id)
    try:
        live.start_slot(db, s, ctx.actor)
    except live.LiveError as exc:
        raise bad_request(str(exc)) from exc
    db.commit()
    return slot_out(s)


@router.post("/slots/{slot_id}/complete", response_model=SlotOut)
def complete(slot_id: int, body: SlotResult, ctx: PanelCtx, db: DbSession) -> SlotOut:
    s = _slot(db, ctx, slot_id)
    try:
        live.complete_slot(db, s, body.result, ctx.actor)
    except live.LiveError as exc:
        raise bad_request(str(exc)) from exc
    db.commit()
    return slot_out(s)


@router.post("/slots/{slot_id}/no-show", response_model=SlotOut)
def no_show(slot_id: int, ctx: PanelCtx, db: DbSession) -> SlotOut:
    s = _slot(db, ctx, slot_id)
    try:
        live.mark_no_show(db, s, ctx.actor)
    except live.LiveError as exc:
        raise bad_request(str(exc)) from exc
    db.commit()
    return slot_out(s)


@router.get("/slots/{slot_id}/resume")
def resume(slot_id: int, ctx: PanelCtx, db: DbSession) -> FileResponse:
    s = _slot(db, ctx, slot_id)
    app = s.application
    r = db.get(Resume, app.resume_id) if app.resume_id else None
    r = r or (app.student.resumes[0] if app.student.resumes else None)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This candidate has no resume on file.")
    return resume_response(r, f"{app.student.roll_no}.pdf")
