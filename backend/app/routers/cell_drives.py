"""Placement cell API for drives: setup, JD rules, rounds and panels, scheduling, drive control room."""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..core.config import get_settings
from ..core.deps import CellUser, DbSession
from ..core.security import create_checkin_token, create_link_token
from ..core.timeutil import ensure_utc, utcnow
from ..models import (
    Application,
    AppStatus,
    Company,
    Drive,
    DriveStatus,
    EligibilityDecision,
    EligibilityRule,
    Offer,
    OfferStatus,
    Panel,
    ProposalStatus,
    ReplanProposal,
    Round,
    Slot,
    SlotStatus,
    Student,
)
from ..schemas import (
    ApplicationOut,
    DriveIn,
    DriveOut,
    DriveUpdate,
    ExtractRequest,
    OfferOut,
    PanelIn,
    PanelOut,
    RoundIn,
    RoundOut,
    RuleIn,
    RuleOut,
    ScheduleRequest,
    SlotOut,
    SlotResult,
    StageUpdate,
)
from ..services import drive_kit, eligibility, fairness, jd_parser, live, notifications, nudges
from ..services.events import emit
from ..services.policy import active_policy
from ..services.workhours import WorkHoursError, WorkingHours, drive_hours, parse_breaks
from .common import application_out, bad_request, conflict, drive_out, get_or_404, offer_out, slot_out

router = APIRouter(prefix="/api/cell/drives", tags=["placement cell: drives"])


def _drive(db: DbSession, drive_id: int) -> Drive:
    return get_or_404(db, Drive, drive_id, "Drive")


def _own(obj_drive_id: int, drive: Drive, what: str) -> None:
    if obj_drive_id != drive.id:
        raise bad_request(f"That {what} belongs to a different drive.")


# ---------------------------------------------------------------- drives CRUD
@router.get("", response_model=list[DriveOut])
def list_drives(user: CellUser, db: DbSession, status_filter: DriveStatus | None = Query(default=None, alias="status")
                ) -> list[DriveOut]:
    _, cfg = active_policy(db)
    stmt = select(Drive).order_by(Drive.id.desc())
    if status_filter:
        stmt = stmt.where(Drive.status == status_filter)
    return [drive_out(db, d, cfg) for d in db.scalars(stmt)]


@router.post("", response_model=DriveOut, status_code=status.HTTP_201_CREATED)
def create_drive(body: DriveIn, user: CellUser, db: DbSession) -> DriveOut:
    company = get_or_404(db, Company, body.company_id, "Company")
    if company.archived:
        raise conflict("This company is archived. Restore it before creating a drive.")
    d = Drive(**body.model_dump(), status=DriveStatus.draft)
    if body.jd_text:
        ext = jd_parser.extract(body.jd_text, get_settings())
        d.skills = ext.skills
        for r in ext.rules:
            d.rules.append(EligibilityRule(field=r.field, op=r.op, value=r.value, source_text=r.source_text,
                                           confidence=r.confidence, approved=False))
    db.add(d)
    db.flush()
    emit(db, "drive.created", user.email, entity="drive", entity_id=d.id, drive_id=d.id)
    eligibility.recompute_drive(db, d)
    db.commit()
    _, cfg = active_policy(db)
    return drive_out(db, d, cfg)


@router.get("/{drive_id}")
def get_drive(drive_id: int, user: CellUser, db: DbSession) -> dict[str, Any]:
    d = _drive(db, drive_id)
    _, cfg = active_policy(db)
    return {
        "drive": drive_out(db, d, cfg).model_dump(mode="json"),
        "rules": [RuleOut.model_validate(r).model_dump(mode="json") for r in d.rules],
        "rounds": [RoundOut.model_validate(r).model_dump(mode="json") for r in d.rounds],
    }


@router.patch("/{drive_id}", response_model=DriveOut)
def update_drive(drive_id: int, body: DriveUpdate, user: CellUser, db: DbSession) -> DriveOut:
    d = _drive(db, drive_id)
    data = body.model_dump(exclude_unset=True)
    new_status = data.pop("status", None)
    for k, v in data.items():
        setattr(d, k, v)
    if new_status and new_status != d.status:
        if new_status == DriveStatus.open and any(not r.approved for r in d.rules):
            raise bad_request("Approve or remove the pending eligibility rules before opening the drive.")
        d.status = new_status
        if new_status == DriveStatus.open:
            eligible_ids = [s for s, in db.execute(select(EligibilityDecision.student_id).where(
                EligibilityDecision.drive_id == d.id, EligibilityDecision.eligible.is_(True)))]
            notifications.notify(db, eligible_ids, f"New drive: {d.company.name}",
                                 f"You're eligible for {d.role} ({d.ctc_lpa:g} LPA). Apply before the deadline.",
                                 link=f"/student/drives/{d.id}")
    if "ctc_lpa" in data or new_status:
        eligibility.recompute_drive(db, d)
    emit(db, "drive.updated", user.email, entity="drive", entity_id=d.id, drive_id=d.id,
         payload={"fields": list(data) + (["status"] if new_status else [])})
    db.commit()
    _, cfg = active_policy(db)
    return drive_out(db, d, cfg)


@router.delete("/{drive_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_drive(drive_id: int, user: CellUser, db: DbSession) -> None:
    d = _drive(db, drive_id)
    d.status = DriveStatus.cancelled
    for s in db.scalars(select(Slot).where(Slot.drive_id == d.id, Slot.status == SlotStatus.scheduled)):
        s.status = SlotStatus.cancelled
    affected = [a.student_id for a in d.applications if a.status in (AppStatus.applied, AppStatus.in_process)]
    notifications.notify(db, affected, f"{d.company.name} drive cancelled",
                         "The placement cell cancelled this drive. Your other applications are unaffected.")
    emit(db, "drive.cancelled", user.email, entity="drive", entity_id=d.id, drive_id=d.id)
    db.commit()


# ---------------------------------------------------------------- eligibility rules
@router.post("/{drive_id}/extract", response_model=list[RuleOut])
def extract_rules(drive_id: int, body: ExtractRequest, user: CellUser, db: DbSession) -> Any:
    d = _drive(db, drive_id)
    ext = jd_parser.extract(body.jd_text, get_settings())
    d.jd_text = body.jd_text
    d.skills = ext.skills
    if ext.ctc_lpa and not d.ctc_lpa:
        d.ctc_lpa = ext.ctc_lpa
    if body.replace_existing:
        d.rules.clear()
        db.flush()
    for r in ext.rules:
        d.rules.append(EligibilityRule(field=r.field, op=r.op, value=r.value, source_text=r.source_text,
                                       confidence=r.confidence, approved=False))
    db.flush()
    emit(db, "rules.extracted", user.email, entity="drive", entity_id=d.id, drive_id=d.id,
         payload={"extractor": ext.extractor, "rules": len(ext.rules), "skills": len(ext.skills)})
    db.commit()
    return d.rules


@router.post("/{drive_id}/rules", response_model=RuleOut, status_code=status.HTTP_201_CREATED)
def add_rule(drive_id: int, body: RuleIn, user: CellUser, db: DbSession) -> Any:
    d = _drive(db, drive_id)
    if any(r.field == body.field for r in d.rules):
        raise bad_request("This drive already has a rule for that field. Edit it instead.")
    r = EligibilityRule(drive_id=d.id, field=body.field, op=body.op, value=body.value,
                        source_text=body.source_text or "Added by the placement cell", confidence=1.0,
                        approved=body.approved, approved_by=user.email if body.approved else None)
    db.add(r)
    db.flush()
    emit(db, "rule.added", user.email, entity="rule", entity_id=r.id, drive_id=d.id)
    eligibility.recompute_drive(db, d)
    db.commit()
    return r


@router.put("/{drive_id}/rules/{rule_id}", response_model=RuleOut)
def edit_rule(drive_id: int, rule_id: int, body: RuleIn, user: CellUser, db: DbSession) -> Any:
    d = _drive(db, drive_id)
    r = get_or_404(db, EligibilityRule, rule_id, "Rule")
    _own(r.drive_id, d, "rule")
    r.field, r.op, r.value = body.field, body.op, body.value
    r.approved = body.approved
    r.approved_by = user.email if body.approved else None
    if body.source_text:
        r.source_text = body.source_text
    emit(db, "rule.updated", user.email, entity="rule", entity_id=r.id, drive_id=d.id,
         payload={"approved": body.approved})
    eligibility.recompute_drive(db, d)
    db.commit()
    return r


@router.post("/{drive_id}/rules/approve-all", response_model=list[RuleOut])
def approve_all(drive_id: int, user: CellUser, db: DbSession) -> Any:
    d = _drive(db, drive_id)
    for r in d.rules:
        if not r.approved:
            r.approved, r.approved_by = True, user.email
    emit(db, "rules.approved", user.email, entity="drive", entity_id=d.id, drive_id=d.id)
    eligibility.recompute_drive(db, d)
    db.commit()
    return d.rules


@router.delete("/{drive_id}/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(drive_id: int, rule_id: int, user: CellUser, db: DbSession) -> None:
    d = _drive(db, drive_id)
    r = get_or_404(db, EligibilityRule, rule_id, "Rule")
    _own(r.drive_id, d, "rule")
    db.delete(r)
    db.flush()
    db.refresh(d)
    emit(db, "rule.removed", user.email, entity="rule", entity_id=rule_id, drive_id=d.id)
    eligibility.recompute_drive(db, d)
    db.commit()


@router.get("/{drive_id}/decisions")
def decisions(drive_id: int, user: CellUser, db: DbSession, eligible: bool | None = None) -> dict[str, Any]:
    d = _drive(db, drive_id)
    stmt = select(EligibilityDecision, Student).join(Student, Student.id == EligibilityDecision.student_id).where(
        EligibilityDecision.drive_id == d.id)
    if eligible is not None:
        stmt = stmt.where(EligibilityDecision.eligible.is_(eligible))
    rows = db.execute(stmt.order_by(Student.roll_no)).all()
    return {"total": len(rows), "items": [
        {"student_id": s.id, "name": s.name, "roll_no": s.roll_no, "branch": s.branch, "cgpa": s.cgpa,
         "backlogs": s.backlogs, "eligible": dec.eligible, "failed": [r for r in dec.reasons if not r["ok"]]}
        for dec, s in rows]}


# ---------------------------------------------------------------- rounds & panels
@router.post("/{drive_id}/rounds", response_model=RoundOut, status_code=status.HTTP_201_CREATED)
def add_round(drive_id: int, body: RoundIn, user: CellUser, db: DbSession) -> Any:
    d = _drive(db, drive_id)
    if any(r.seq == body.seq for r in d.rounds):
        raise bad_request(f"Round {body.seq} already exists. Pick another order number.")
    r = Round(drive_id=d.id, **body.model_dump())
    db.add(r)
    db.flush()
    emit(db, "round.added", user.email, entity="round", entity_id=r.id, drive_id=d.id)
    db.commit()
    return r


@router.put("/{drive_id}/rounds/{round_id}", response_model=RoundOut)
def edit_round(drive_id: int, round_id: int, body: RoundIn, user: CellUser, db: DbSession) -> Any:
    d = _drive(db, drive_id)
    r = get_or_404(db, Round, round_id, "Round")
    _own(r.drive_id, d, "round")
    if any(o.seq == body.seq and o.id != r.id for o in d.rounds):
        raise bad_request(f"Round {body.seq} already exists.")
    for k, v in body.model_dump().items():
        setattr(r, k, v)
    emit(db, "round.updated", user.email, entity="round", entity_id=r.id, drive_id=d.id)
    db.commit()
    return r


@router.delete("/{drive_id}/rounds/{round_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_round(drive_id: int, round_id: int, user: CellUser, db: DbSession) -> None:
    d = _drive(db, drive_id)
    r = get_or_404(db, Round, round_id, "Round")
    _own(r.drive_id, d, "round")
    if db.scalar(select(Slot.id).where(Slot.round_id == r.id, Slot.status.in_(
            (SlotStatus.in_progress, SlotStatus.done))).limit(1)):
        raise bad_request("This round already has interviews in progress or done; it can't be deleted.")
    db.delete(r)
    emit(db, "round.removed", user.email, entity="round", entity_id=round_id, drive_id=d.id)
    db.commit()


@router.post("/{drive_id}/panels", response_model=PanelOut, status_code=status.HTTP_201_CREATED)
def add_panel(drive_id: int, body: PanelIn, user: CellUser, db: DbSession) -> Any:
    d = _drive(db, drive_id)
    rnd = get_or_404(db, Round, body.round_id, "Round")
    _own(rnd.drive_id, d, "round")
    p = Panel(drive_id=d.id, **body.model_dump())
    db.add(p)
    db.flush()
    emit(db, "panel.added", user.email, entity="panel", entity_id=p.id, drive_id=d.id)
    db.commit()
    return p


@router.delete("/{drive_id}/panels/{panel_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_panel(drive_id: int, panel_id: int, user: CellUser, db: DbSession) -> None:
    d = _drive(db, drive_id)
    p = get_or_404(db, Panel, panel_id, "Panel")
    _own(p.drive_id, d, "panel")
    if db.scalar(select(Slot.id).where(Slot.panel_id == p.id, Slot.status.in_(
            (SlotStatus.scheduled, SlotStatus.in_progress))).limit(1)):
        raise bad_request("Move this panel's candidates first (re-plan or reschedule the round).")
    db.delete(p)
    emit(db, "panel.removed", user.email, entity="panel", entity_id=panel_id, drive_id=d.id)
    db.commit()


# ---------------------------------------------------------------- applications
@router.get("/{drive_id}/applications", response_model=list[ApplicationOut])
def applications(drive_id: int, user: CellUser, db: DbSession) -> list[ApplicationOut]:
    d = _drive(db, drive_id)
    return [application_out(a) for a in sorted(d.applications, key=lambda a: a.student.roll_no)]


def apply_stage_update(db: DbSession, drive: Drive, body: StageUpdate, actor: str) -> int:
    """Shared by the cell and the recruiter link."""
    apps = {a.id: a for a in drive.applications}
    rnd = None
    if body.action in ("pass_round", "fail_round"):
        if body.round_id is None:
            raise bad_request("Pick the round these results are for.")
        rnd = get_or_404(db, Round, body.round_id, "Round")
        _own(rnd.drive_id, drive, "round")
    changed = 0
    passed: list[Application] = []
    for app_id in body.application_ids:
        a = apps.get(app_id)
        if a is None:
            raise bad_request(f"Application {app_id} is not part of this drive.")
        if body.action == "shortlist" and a.status == AppStatus.applied:
            a.status = AppStatus.in_process
        elif body.action == "reject" and a.status in (AppStatus.applied, AppStatus.in_process):
            # Rejected at screening (None) or at the round the candidate was waiting for.
            a.rejected_round = a.rounds_cleared + 1 if a.status == AppStatus.in_process else None
            a.status = AppStatus.rejected
        elif rnd is not None and a.status == AppStatus.in_process and a.rounds_cleared == rnd.seq - 1:
            if body.action == "pass_round":
                a.rounds_cleared = rnd.seq
                if rnd.seq >= max(r.seq for r in drive.rounds):
                    a.status = AppStatus.selected
                passed.append(a)
            else:
                a.status, a.rejected_round = AppStatus.rejected, rnd.seq
        else:
            continue
        changed += 1
        emit(db, f"application.{body.action}", actor, entity="application", entity_id=a.id, drive_id=drive.id,
             student_id=a.student_id)
    # a rejected candidate gives up any interview slots still waiting
    dropped = [a.id for a in apps.values() if a.status == AppStatus.rejected and a.id in body.application_ids]
    if dropped:
        waiting = select(Slot).where(Slot.application_id.in_(dropped), Slot.status == SlotStatus.scheduled)
        for slot in db.scalars(waiting):
            slot.status = SlotStatus.cancelled
    if changed:
        title = {"shortlist": "You're shortlisted", "reject": "Application update",
                 "pass_round": "You cleared a round", "fail_round": "Round result"}[body.action]
        msg = {"shortlist": f"{drive.company.name} shortlisted you. Watch your timeline for the next round.",
               "reject": f"{drive.company.name} won't be taking your application further this time.",
               "pass_round": f"You cleared {rnd.name if rnd else 'the round'} for {drive.company.name}.",
               "fail_round": f"You didn't clear {rnd.name if rnd else 'the round'} for {drive.company.name}."}
        notifications.notify(db, [apps[i].student_id for i in body.application_ids if i in apps],
                             title, msg[body.action], link="/student")
        db.flush()
        for a in passed:
            if a.status == AppStatus.in_process:
                live.place_in_next_round(db, a, actor)
    return changed


@router.post("/{drive_id}/applications/stage")
def stage_update(drive_id: int, body: StageUpdate, user: CellUser, db: DbSession) -> dict[str, int]:
    d = _drive(db, drive_id)
    changed = apply_stage_update(db, d, body, user.email)
    db.commit()
    return {"changed": changed}


# ---------------------------------------------------------------- scheduling & control room
@router.post("/{drive_id}/schedule")
def schedule_round(drive_id: int, body: ScheduleRequest, user: CellUser, db: DbSession) -> dict[str, Any]:
    d = _drive(db, drive_id)
    rnd = get_or_404(db, Round, body.round_id, "Round")
    _own(rnd.drive_id, d, "round")
    start = ensure_utc(body.start_at)
    assert start
    base = drive_hours(db, d.id)
    try:
        hours = WorkingHours(
            day_start=body.day_start or base.day_start, day_end=body.day_end or base.day_end,
            breaks=tuple(parse_breaks(body.breaks)) if body.breaks is not None else base.breaks,
            max_days=body.max_days or base.max_days,
            skip_sundays=base.skip_sundays if body.skip_sundays is None else body.skip_sundays,
            utc_offset_min=base.utc_offset_min)
    except WorkHoursError as exc:
        raise bad_request(str(exc)) from exc
    try:
        plan = live.schedule_round(db, d, rnd, start, user.email, hours)
    except live.LiveError as exc:
        raise bad_request(str(exc)) from exc
    db.commit()
    return {"round_id": plan.round_id, "slots": plan.slots,
            "finish": plan.finish.isoformat() if plan.finish else None,
            "solve_ms": plan.solve_ms, "optimal": plan.optimal, "days": plan.days or [], "hours": plan.hours}


@router.get("/{drive_id}/work-hours")
def work_hours(drive_id: int, user: CellUser, db: DbSession) -> dict[str, Any]:
    """Hours the Schedule tab starts from: this drive's last ones, else the defaults."""
    d = _drive(db, drive_id)
    return drive_hours(db, d.id).to_payload()


@router.get("/{drive_id}/slots", response_model=list[SlotOut])
def slots(drive_id: int, user: CellUser, db: DbSession) -> list[SlotOut]:
    d = _drive(db, drive_id)
    proj = live.drive_projection(db, d)
    by_slot = {sp.slot.id: sp for items in proj.values() for sp in items}
    rows = db.scalars(select(Slot).where(Slot.drive_id == d.id, Slot.status != SlotStatus.cancelled)
                      .order_by(Slot.start_at)).all()
    return [slot_out(s, by_slot.get(s.id)) for s in rows]


@router.get("/{drive_id}/timeline", response_model=list[SlotOut])
def timeline(drive_id: int, user: CellUser, db: DbSession) -> list[SlotOut]:
    """Same as /slots, kept as a stable name for the Gantt view."""
    return slots(drive_id, user, db)


@router.get("/{drive_id}/control-room")
def control_room(drive_id: int, user: CellUser, db: DbSession) -> dict[str, Any]:
    d = _drive(db, drive_id)
    now = utcnow()
    proj = live.drive_projection(db, d, now)
    holds = live.panel_holds(db, d.id, now)
    panels = []
    for rnd in d.rounds:
        for p in rnd.panels:
            items = proj.get(p.id, [])
            done = db.scalars(select(Slot).where(Slot.panel_id == p.id, Slot.status == SlotStatus.done)).all()
            panels.append({
                "id": p.id, "name": p.name, "room": p.room, "round": rnd.name, "round_id": rnd.id,
                "duration_min": rnd.duration_min, "interviewer": p.interviewer, "done": len(done),
                "now": slot_out(items[0].slot, items[0]).model_dump(mode="json")
                if items and items[0].slot.status == SlotStatus.in_progress else None,
                "queue": [slot_out(sp.slot, sp).model_dump(mode="json") for sp in items
                          if sp.slot.status == SlotStatus.scheduled],
                "late_min": max((sp.lateness_min for sp in items), default=0),
                "hold": None if p.id not in holds else
                {"until": holds[p.id].isoformat() if holds[p.id] else None},  # type: ignore[union-attr]
                "finish": max((sp.projected_end for sp in items), default=None),
            })
    for p in panels:
        p["finish"] = p["finish"].isoformat() if p["finish"] else None
    proposals = db.scalars(select(ReplanProposal).where(ReplanProposal.drive_id == d.id)
                           .order_by(ReplanProposal.id.desc()).limit(5)).all()
    no_shows = db.scalars(select(Slot).where(Slot.drive_id == d.id, Slot.status == SlotStatus.no_show)).all()
    total_slots = db.scalars(select(Slot).where(Slot.drive_id == d.id, Slot.status != SlotStatus.cancelled)).all()
    return {
        "drive": {"id": d.id, "company": d.company.name, "role": d.role, "status": d.status.value},
        "now": now.isoformat(),
        "panels": panels,
        "proposals": [{"id": p.id, "reason": p.reason, "status": p.status.value, "changes": p.changes,
                       "metrics": p.metrics, "created_at": p.created_at.isoformat()} for p in proposals],
        "no_shows": [slot_out(s).model_dump(mode="json") for s in no_shows],
        "progress": {"done": sum(s.status == SlotStatus.done for s in total_slots),
                     "checked_in": sum(s.checked_in_at is not None and s.status == SlotStatus.scheduled
                                       for s in total_slots),
                     "total": len(total_slots)},
    }


@router.post("/{drive_id}/replan")
def request_replan(drive_id: int, user: CellUser, db: DbSession) -> dict[str, Any]:
    d = _drive(db, drive_id)
    p = live.propose_replan(db, d, "Requested by the placement cell", user.email)
    db.commit()
    if p is None:
        return {"proposal": None, "message": "The current plan is already the best one. No changes needed."}
    return {"proposal": p.id, "changes": len(p.changes), "metrics": p.metrics}


class DisruptIn(BaseModel):
    kind: Literal["overrun", "panel_down", "no_show", "release"]
    panel_id: int | None = None
    minutes: int = Field(default=30, ge=5, le=180)
    auto_apply: bool = True


@router.post("/{drive_id}/disrupt")
def disrupt(drive_id: int, body: DisruptIn, user: CellUser, db: DbSession) -> dict[str, Any]:
    """Inject a disruption (overrun, panel down, no-show) and let CP-SAT re-plan the rest of the day live."""
    d = _drive(db, drive_id)
    try:
        result = live.disrupt(db, d, body.kind, user.email, panel_id=body.panel_id, minutes=body.minutes,
                              auto_apply=body.auto_apply)
    except live.LiveError as exc:
        raise bad_request(str(exc)) from exc
    db.commit()
    return result


@router.get("/{drive_id}/fairness")
def fairness_audit(drive_id: int, user: CellUser, db: DbSession) -> dict[str, Any]:
    """Who each eligibility rule excludes, near misses, a CGPA cutoff sweep and four-fifths-rule flags."""
    return fairness.drive_fairness(db, _drive(db, drive_id))


@router.post("/{drive_id}/proposals/{proposal_id}/{decision}")
def decide_proposal(drive_id: int, proposal_id: int, decision: str, user: CellUser, db: DbSession
                    ) -> dict[str, Any]:
    d = _drive(db, drive_id)
    p = get_or_404(db, ReplanProposal, proposal_id, "Proposal")
    _own(p.drive_id, d, "proposal")
    if decision == "approve":
        try:
            applied = live.apply_proposal(db, p, user.email)
        except live.LiveError as exc:
            raise bad_request(str(exc)) from exc
        db.commit()
        return {"status": "approved", "applied": applied}
    if decision == "dismiss":
        if p.status != ProposalStatus.pending:
            raise bad_request("This proposal is no longer pending.")
        p.status, p.decided_by, p.decided_at = ProposalStatus.dismissed, user.email, utcnow()
        emit(db, "replan.dismissed", user.email, entity="proposal", entity_id=p.id, drive_id=d.id)
        db.commit()
        return {"status": "dismissed"}
    raise bad_request("Decision must be approve or dismiss.")


@router.post("/{drive_id}/slots/{slot_id}/{action}", response_model=SlotOut)
def slot_action(drive_id: int, slot_id: int, action: str, user: CellUser, db: DbSession,
                body: SlotResult | None = None) -> SlotOut:
    d = _drive(db, drive_id)
    s = get_or_404(db, Slot, slot_id, "Slot")
    _own(s.drive_id, d, "slot")
    try:
        if action == "checkin":
            live.check_in(db, s, user.email)
        elif action == "start":
            live.start_slot(db, s, user.email)
        elif action == "complete":
            if body is None:
                raise bad_request("Send the result (pass or fail).")
            live.complete_slot(db, s, body.result, user.email)
        elif action == "no-show":
            live.mark_no_show(db, s, user.email)
        else:
            raise bad_request("Unknown action.")
    except live.LiveError as exc:
        raise bad_request(str(exc)) from exc
    db.commit()
    return slot_out(s)


@router.get("/{drive_id}/nudge")
def nudge_preview(drive_id: int, user: CellUser, db: DbSession) -> dict[str, Any]:
    """How many eligible students have not applied, and when they were last reminded."""
    d = _drive(db, drive_id)
    last = nudges.last_nudge(db, d)
    return {"pending": len(nudges.pending_students(db, d)), "open": d.status == DriveStatus.open,
            "last_sent_at": last.ts.isoformat() if last else None,
            "last_count": (last.payload or {}).get("students") if last else None}


@router.post("/{drive_id}/nudge")
def nudge_now(drive_id: int, user: CellUser, db: DbSession) -> dict[str, int]:
    d = _drive(db, drive_id)
    if d.status != DriveStatus.open:
        raise bad_request("Reminders can only be sent while applications are open.")
    n = nudges.send_nudge(db, d, user.email)
    db.commit()
    return {"sent": n}


@router.get("/{drive_id}/drive-kit.pdf")
def drive_kit_pdf(drive_id: int, user: CellUser, db: DbSession) -> Response:
    d = _drive(db, drive_id)
    pdf = drive_kit.kit_pdf(db, d)
    name = f"drive_kit_{d.company.name}_{d.id}".replace(" ", "_")
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})


@router.get("/{drive_id}/checkin-code")
def checkin_code(drive_id: int, user: CellUser, db: DbSession) -> dict[str, Any]:
    d = _drive(db, drive_id)
    token = create_checkin_token(d.id)
    return {"url": f"{get_settings().public_url}/checkin?t={token}", "token": token,
            "expires_at": (utcnow() + timedelta(seconds=90)).isoformat()}


@router.get("/{drive_id}/links")
def links(drive_id: int, user: CellUser, db: DbSession) -> dict[str, Any]:
    d = _drive(db, drive_id)
    base = get_settings().public_url
    return {
        "recruiter": f"{base}/r/{create_link_token('recruiter', d.id, d.link_version)}",
        "panels": [{"panel_id": p.id, "name": p.name, "round": r.name,
                    "url": f"{base}/p/{create_link_token('panel', d.id, d.link_version, p.id)}"}
                   for r in d.rounds for p in r.panels],
        "version": d.link_version,
    }


@router.post("/{drive_id}/links/rotate")
def rotate_links(drive_id: int, user: CellUser, db: DbSession) -> dict[str, Any]:
    d = _drive(db, drive_id)
    d.link_version += 1
    emit(db, "links.rotated", user.email, entity="drive", entity_id=d.id, drive_id=d.id)
    db.commit()
    return links(drive_id, user, db)


@router.get("/{drive_id}/offers", response_model=list[OfferOut])
def drive_offers(drive_id: int, user: CellUser, db: DbSession) -> list[OfferOut]:
    d = _drive(db, drive_id)
    _, cfg = active_policy(db)
    return [offer_out(a, cfg) for a in d.applications if a.offer is not None]


@router.post("/{drive_id}/offers/{offer_id}/revoke", response_model=OfferOut)
def revoke_offer(drive_id: int, offer_id: int, user: CellUser, db: DbSession) -> OfferOut:
    d = _drive(db, drive_id)
    o = get_or_404(db, Offer, offer_id, "Offer")
    _own(o.application.drive_id, d, "offer")
    o.status = OfferStatus.revoked
    o.decided_at = utcnow()
    emit(db, "offer.revoked", user.email, entity="offer", entity_id=o.id, drive_id=d.id,
         student_id=o.application.student_id)
    eligibility.recompute_for_student(db, o.application.student, db.scalars(
        select(Drive).where(Drive.status.in_((DriveStatus.open, DriveStatus.draft)))).all())
    db.commit()
    _, cfg = active_policy(db)
    return offer_out(o.application, cfg)

