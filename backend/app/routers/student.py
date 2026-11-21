"""Student-facing API: 'tell me where I stand'. Every route is scoped to the signed-in student's own data."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import func, select

from ..core.config import get_settings
from ..core.deps import DbSession, StudentCtx
from ..core.security import TokenError, decode_checkin_token
from ..core.timeutil import ensure_utc, utcnow
from ..models import (
    Application,
    AppStatus,
    Dispute,
    DisputeStatus,
    Drive,
    DriveStatus,
    EligibilityDecision,
    Experience,
    Notification,
    Offer,
    OfferStatus,
    Resume,
    Slot,
    SlotStatus,
)
from ..schemas import (
    ApplicationOut,
    ApplyIn,
    CheckinIn,
    DisputeIn,
    ExperienceIn,
    OfferOut,
    QuestionIn,
    ResumeOut,
    SlotOut,
    StudentOut,
    StudentSelfUpdate,
)
from ..services import analytics, company_profile, eligibility, insights, live, offer_sim, qa
from ..services.events import emit
from ..services.exports import slots_to_ics
from ..services.policy import active_policy, placement_for
from .common import application_out, bad_request, drive_out, get_or_404, offer_out, resume_response, slot_out

router = APIRouter(prefix="/api/me", tags=["student"])

MAX_RESUME_BYTES = 2 * 1024 * 1024
VISIBLE = (DriveStatus.open, DriveStatus.closed, DriveStatus.live, DriveStatus.completed)


def _own_application(db: DbSession, ctx: StudentCtx, drive_id: int) -> Application | None:
    return db.scalar(select(Application).where(Application.drive_id == drive_id,
                                               Application.student_id == ctx.student.id))


@router.get("/dashboard")
def dashboard(ctx: StudentCtx, db: DbSession) -> dict[str, Any]:
    return insights.student_dashboard(db, ctx.student)


@router.get("/calendar")
def calendar(ctx: StudentCtx, db: DbSession) -> list[dict[str, Any]]:
    return analytics.student_calendar(db, ctx.student)


@router.get("/analytics")
def my_analytics(ctx: StudentCtx, db: DbSession) -> dict[str, Any]:
    return analytics.student_analytics(db, ctx.student)


@router.get("/profile", response_model=StudentOut)
def profile(ctx: StudentCtx) -> Any:
    return ctx.student


@router.patch("/profile", response_model=StudentOut)
def update_profile(body: StudentSelfUpdate, ctx: StudentCtx, db: DbSession) -> Any:
    # Academic fields (CGPA, backlogs, branch) are owned by the cell's records and can't be self-edited.
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(ctx.student, k, v)
    emit(db, "student.profile_updated", ctx.user.email, entity="student", entity_id=ctx.student.id,
         payload={"fields": list(data)})
    db.commit()
    return ctx.student


# ---------------------------------------------------------------- resumes
@router.get("/resumes", response_model=list[ResumeOut])
def resumes(ctx: StudentCtx) -> Any:
    return ctx.student.resumes


@router.post("/resumes", response_model=ResumeOut, status_code=status.HTTP_201_CREATED)
async def upload_resume(ctx: StudentCtx, db: DbSession, track: str = Form(..., min_length=2, max_length=40),
                        file: UploadFile = File(...)) -> Any:
    content = await file.read(MAX_RESUME_BYTES + 1)
    if len(content) > MAX_RESUME_BYTES:
        raise bad_request("Resumes must be 2 MB or smaller.")
    if not content.startswith(b"%PDF"):
        raise bad_request("Upload your resume as a PDF.")
    if len(ctx.student.resumes) >= 5:
        raise bad_request("You can keep up to 5 resumes. Delete one first.")
    root = Path(get_settings().upload_dir)
    rel = Path("resumes") / f"{ctx.student.id}_{uuid.uuid4().hex}.pdf"
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(content)
    safe_name = Path(file.filename or "resume.pdf").name[:180]
    r = Resume(student_id=ctx.student.id, track=track.strip(), filename=safe_name, path=str(rel))
    db.add(r)
    emit(db, "resume.uploaded", ctx.user.email, entity="student", entity_id=ctx.student.id)
    db.commit()
    return r


@router.get("/resumes/{resume_id}")
def download_resume(resume_id: int, ctx: StudentCtx, db: DbSession) -> FileResponse:
    r = get_or_404(db, Resume, resume_id, "Resume")
    if r.student_id != ctx.student.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resume not found.")
    return resume_response(r, r.filename)


@router.delete("/resumes/{resume_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resume(resume_id: int, ctx: StudentCtx, db: DbSession) -> None:
    r = get_or_404(db, Resume, resume_id, "Resume")
    if r.student_id != ctx.student.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resume not found.")
    path = Path(get_settings().upload_dir) / r.path
    db.delete(r)
    db.commit()
    path.unlink(missing_ok=True)


# ---------------------------------------------------------------- drives
@router.get("/drives")
def my_drives(ctx: StudentCtx, db: DbSession) -> list[dict[str, Any]]:
    _, cfg = active_policy(db)
    drives = db.scalars(select(Drive).where(Drive.status.in_(VISIBLE)).order_by(Drive.apply_deadline)).all()
    decisions = {d.drive_id: d for d in db.scalars(
        select(EligibilityDecision).where(EligibilityDecision.student_id == ctx.student.id))}
    apps = {a.drive_id: a for a in ctx.student.applications}
    out = []
    for d in drives:
        dec = decisions.get(d.id)
        app = apps.get(d.id)
        out.append({
            "drive": drive_out(db, d, cfg, with_counts=False).model_dump(mode="json"),
            "eligible": dec.eligible if dec else None,
            "reasons": dec.reasons if dec else [],
            "application": application_out(app).model_dump(mode="json") if app else None,
        })
    return out


@router.get("/drives/{drive_id}/company-profile")
def drive_company_profile(drive_id: int, ctx: StudentCtx, db: DbSession) -> dict[str, Any]:
    """FUT-4: what to expect from this company, from its past drives here (aggregates only)."""
    d = get_or_404(db, Drive, drive_id, "Drive")
    if d.status not in VISIBLE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Drive not found.")
    return company_profile.company_profile(db, d.company, exclude_drive_id=d.id)


@router.get("/drives/{drive_id}")
def drive_detail(drive_id: int, ctx: StudentCtx, db: DbSession) -> dict[str, Any]:
    _, cfg = active_policy(db)
    d = get_or_404(db, Drive, drive_id, "Drive")
    if d.status not in VISIBLE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Drive not found.")
    dec = db.get(EligibilityDecision, (d.id, ctx.student.id))
    app = _own_application(db, ctx, d.id)
    exp = db.execute(select(func.avg(Experience.difficulty), func.count()).where(Experience.drive_id == d.id)).one()
    topics: dict[str, int] = {}
    for e in db.scalars(select(Experience).where(Experience.drive_id == d.id)):
        for t in e.topics:
            topics[t.lower()] = topics.get(t.lower(), 0) + 1
    dispute = db.scalar(select(Dispute).where(Dispute.drive_id == d.id, Dispute.student_id == ctx.student.id)
                        .order_by(Dispute.id.desc()))
    return {
        "drive": drive_out(db, d, cfg, with_counts=False).model_dump(mode="json"),
        "eligible": dec.eligible if dec else None,
        "reasons": dec.reasons if dec else [],
        "rounds": [{"name": r.name, "kind": r.kind.value, "seq": r.seq, "duration_min": r.duration_min}
                   for r in d.rounds],
        "application": application_out(app).model_dump(mode="json") if app else None,
        "dispute": {"status": dispute.status.value, "resolution": dispute.resolution} if dispute else None,
        "experience": {"count": exp[1], "avg_difficulty": round(float(exp[0]), 1) if exp[0] else None,
                       "topics": sorted(topics.items(), key=lambda kv: -kv[1])[:8]},
    }


def _refresh_if_placed(db: DbSession, ctx: StudentCtx) -> None:
    """A placed student's applications use up upgrade chances (P6), so their eligibility changes everywhere."""
    if placement_for(db, ctx.student.id).best_ctc is not None:
        upcoming = db.scalars(select(Drive).where(Drive.status.in_((DriveStatus.open, DriveStatus.draft)))).all()
        eligibility.recompute_for_student(db, ctx.student, upcoming)


@router.post("/drives/{drive_id}/apply", response_model=ApplicationOut, status_code=status.HTTP_201_CREATED)
def apply(drive_id: int, body: ApplyIn, ctx: StudentCtx, db: DbSession) -> ApplicationOut:
    d = get_or_404(db, Drive, drive_id, "Drive")
    if d.status != DriveStatus.open:
        raise bad_request("Applications for this drive are closed.")
    if d.apply_deadline and ensure_utc(d.apply_deadline) < utcnow():  # type: ignore[operator]
        raise bad_request("The application deadline has passed.")
    _, cfg = active_policy(db)
    decision = eligibility.evaluate(eligibility.StudentProfile.of(ctx.student), eligibility.rule_specs(d.rules),
                                    cfg, placement_for(db, ctx.student.id, cfg), d.ctc_lpa, d.id)
    if not decision.eligible:
        raise bad_request("You're not eligible: " + "; ".join(r.message for r in decision.failed))
    resume_id = body.resume_id or (ctx.student.resumes[0].id if ctx.student.resumes else None)
    if resume_id is None:
        raise bad_request("Upload a resume before applying.")
    if not any(r.id == resume_id for r in ctx.student.resumes):
        raise bad_request("Pick one of your own resumes.")
    app = _own_application(db, ctx, d.id)
    if app is not None and app.status != AppStatus.withdrawn:
        raise bad_request("You've already applied to this drive.")
    if app is None:
        app = Application(drive_id=d.id, student_id=ctx.student.id, resume_id=resume_id)
        db.add(app)
    else:
        app.status, app.resume_id, app.rounds_cleared, app.rejected_round = AppStatus.applied, resume_id, 0, None
    db.flush()
    emit(db, "application.created", ctx.user.email, entity="application", entity_id=app.id, drive_id=d.id,
         student_id=ctx.student.id)
    _refresh_if_placed(db, ctx)
    db.commit()
    return application_out(app)


@router.post("/drives/{drive_id}/withdraw", response_model=ApplicationOut)
def withdraw(drive_id: int, ctx: StudentCtx, db: DbSession) -> ApplicationOut:
    app = _own_application(db, ctx, drive_id)
    if app is None or app.status not in (AppStatus.applied, AppStatus.in_process):
        raise bad_request("There's no active application to withdraw.")
    app.status = AppStatus.withdrawn
    for s in db.scalars(select(Slot).where(Slot.application_id == app.id, Slot.status == SlotStatus.scheduled)):
        s.status = SlotStatus.cancelled
    emit(db, "application.withdrawn", ctx.user.email, entity="application", entity_id=app.id, drive_id=drive_id,
         student_id=ctx.student.id)
    db.flush()
    _refresh_if_placed(db, ctx)
    db.commit()
    return application_out(app)


@router.post("/disputes", status_code=status.HTTP_201_CREATED)
def raise_dispute(body: DisputeIn, ctx: StudentCtx, db: DbSession) -> dict[str, Any]:
    get_or_404(db, Drive, body.drive_id, "Drive")
    open_one = db.scalar(select(Dispute).where(Dispute.drive_id == body.drive_id,
                                               Dispute.student_id == ctx.student.id,
                                               Dispute.status == DisputeStatus.open))
    if open_one:
        raise bad_request("You already have an open dispute for this drive.")
    d = Dispute(student_id=ctx.student.id, drive_id=body.drive_id, message=body.message.strip())
    db.add(d)
    db.flush()
    emit(db, "dispute.raised", ctx.user.email, entity="dispute", entity_id=d.id, drive_id=body.drive_id)
    db.commit()
    return {"id": d.id, "status": d.status.value}


# ---------------------------------------------------------------- schedule & check-in
def _my_slots(db: DbSession, ctx: StudentCtx) -> list[Slot]:
    return list(db.scalars(
        select(Slot).join(Application, Slot.application_id == Application.id)
        .where(Application.student_id == ctx.student.id, Slot.status != SlotStatus.cancelled)
        .order_by(Slot.start_at)
    ).all())


@router.get("/schedule", response_model=list[SlotOut])
def schedule(ctx: StudentCtx, db: DbSession) -> list[SlotOut]:
    slots = _my_slots(db, ctx)
    out = []
    cache: dict[int, dict[int, list[live.SlotProjection]]] = {}
    for s in slots:
        if s.drive_id not in cache:
            cache[s.drive_id] = live.drive_projection(db, s.application.drive)
        proj = next((p for p in cache[s.drive_id].get(s.panel_id, []) if p.slot.id == s.id), None)
        out.append(slot_out(s, proj))
    return out


@router.get("/schedule.ics")
def schedule_ics(ctx: StudentCtx, db: DbSession) -> PlainTextResponse:
    slots = [s for s in _my_slots(db, ctx) if s.status in (SlotStatus.scheduled, SlotStatus.in_progress)]
    return PlainTextResponse(slots_to_ics(slots, utcnow()), media_type="text/calendar",
                             headers={"Content-Disposition": 'attachment; filename="driveops.ics"'})


@router.post("/checkin")
def checkin(body: CheckinIn, ctx: StudentCtx, db: DbSession) -> dict[str, Any]:
    """Student scans the rotating venue QR with their phone; we check in their next slot at that drive."""
    try:
        drive_id = decode_checkin_token(body.token)
    except TokenError as exc:
        raise bad_request("This check-in code has expired. Scan the QR on the screen again.") from exc
    slot = db.scalar(
        select(Slot).join(Application, Slot.application_id == Application.id)
        .where(Application.student_id == ctx.student.id, Slot.drive_id == drive_id,
               Slot.status.in_((SlotStatus.scheduled, SlotStatus.no_show)))
        .order_by(Slot.start_at)
    )
    if slot is None:
        raise bad_request("You don't have an upcoming interview at this drive.")
    if slot.checked_in_at is not None and slot.status == SlotStatus.scheduled:
        return {"slot_id": slot.id, "already": True, "panel": slot.panel.name, "room": slot.panel.room}
    try:
        live.check_in(db, slot, ctx.user.email)
    except live.LiveError as exc:
        raise bad_request(str(exc)) from exc
    db.commit()
    return {"slot_id": slot.id, "already": False, "panel": slot.panel.name, "room": slot.panel.room}


# ---------------------------------------------------------------- offers
@router.get("/offers", response_model=list[OfferOut])
def offers(ctx: StudentCtx, db: DbSession) -> list[OfferOut]:
    _, cfg = active_policy(db)
    return [offer_out(a, cfg) for a in ctx.student.applications if a.offer is not None]


def _decide(offer_id: int, accept: bool, ctx: StudentCtx, db: DbSession) -> OfferOut:
    offer = get_or_404(db, Offer, offer_id, "Offer")
    app = offer.application
    if app.student_id != ctx.student.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Offer not found.")
    if offer.status != OfferStatus.pending:
        raise bad_request("You've already responded to this offer.")
    if ensure_utc(offer.deadline) < utcnow():  # type: ignore[operator]
        raise bad_request("The deadline for this offer has passed. Contact the placement cell.")
    _, cfg = active_policy(db)
    if accept:
        held = placement_for(db, ctx.student.id)
        if len(held.accepted_ctcs) >= cfg.max_offers:
            raise bad_request(f"You've reached the limit of {cfg.max_offers} accepted offer(s) (P2).")
    offer.status = OfferStatus.accepted if accept else OfferStatus.declined
    offer.decided_at = utcnow()
    db.flush()
    emit(db, "offer.accepted" if accept else "offer.declined", ctx.user.email, entity="offer", entity_id=offer.id,
         drive_id=app.drive_id, student_id=ctx.student.id)
    if accept:
        # Policy lock: accepting changes this student's eligibility for every upcoming drive.
        upcoming = db.scalars(select(Drive).where(Drive.status.in_((DriveStatus.open, DriveStatus.draft)))).all()
        eligibility.recompute_for_student(db, ctx.student, upcoming)
    db.commit()
    return offer_out(app, cfg)


@router.get("/offers/{offer_id}/what-if")
def offer_what_if(offer_id: int, ctx: StudentCtx, db: DbSession) -> dict[str, Any]:
    """What accepting or declining this offer does to the rest of your season, with rough odds."""
    offer = get_or_404(db, Offer, offer_id, "Offer")
    if offer.application.student_id != ctx.student.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Offer not found.")
    return offer_sim.offer_whatif(db, ctx.student, offer)


@router.post("/offers/{offer_id}/accept", response_model=OfferOut)
def accept_offer(offer_id: int, ctx: StudentCtx, db: DbSession) -> OfferOut:
    return _decide(offer_id, True, ctx, db)


@router.post("/offers/{offer_id}/decline", response_model=OfferOut)
def decline_offer(offer_id: int, ctx: StudentCtx, db: DbSession) -> OfferOut:
    return _decide(offer_id, False, ctx, db)


# ---------------------------------------------------------------- notifications, Q&A, experiences
@router.get("/notifications")
def notifications(ctx: StudentCtx, db: DbSession) -> list[dict[str, Any]]:
    rows = db.scalars(select(Notification).where(Notification.student_id == ctx.student.id)
                      .order_by(Notification.id.desc()).limit(50)).all()
    return [{"id": n.id, "title": n.title, "body": n.body, "link": n.link, "read": n.read,
             "created_at": n.created_at.isoformat()} for n in rows]


@router.post("/notifications/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_read(ctx: StudentCtx, db: DbSession) -> None:
    for n in db.scalars(select(Notification).where(Notification.student_id == ctx.student.id,
                                                   Notification.read.is_(False))):
        n.read = True
    db.commit()


@router.get("/policy")
def policy_rules(ctx: StudentCtx, db: DbSession) -> list[dict[str, str]]:
    """The placement policy in force, as the numbered rules (P0, P1, ...) answers cite."""
    _, cfg = active_policy(db)
    return cfg.describe()


@router.post("/ask")
def ask(body: QuestionIn, ctx: StudentCtx, db: DbSession) -> dict[str, Any]:
    a = qa.answer(db, ctx.student, body.question)
    emit(db, "qa.asked", ctx.user.email, entity="student", entity_id=ctx.student.id,
         payload={"question": body.question[:200]})
    db.commit()
    return {"answer": a.answer, "citations": a.citations, "drives": a.drives, "engine": a.engine}


@router.post("/experiences", status_code=status.HTTP_201_CREATED)
def share_experience(body: ExperienceIn, ctx: StudentCtx, db: DbSession) -> dict[str, Any]:
    app = _own_application(db, ctx, body.drive_id)
    if app is None or app.rounds_cleared == 0 and app.rejected_round is None:
        raise bad_request("You can share an experience after you've taken at least one round of this drive.")
    exp = db.scalar(select(Experience).where(Experience.drive_id == body.drive_id,
                                             Experience.student_id == ctx.student.id))
    topics = sorted({t.strip()[:40] for t in body.topics if t.strip()})
    if exp is None:
        exp = Experience(student_id=ctx.student.id, drive_id=body.drive_id, difficulty=body.difficulty,
                         topics=topics, tips=body.tips)
        db.add(exp)
    else:
        exp.difficulty, exp.topics, exp.tips = body.difficulty, topics, body.tips
    db.commit()
    return {"id": exp.id}

