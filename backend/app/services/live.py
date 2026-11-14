"""Drive-day operations: scheduling rounds, live projections, re-plan proposals and slot state changes.

This module is the bridge between the database and the pure CP-SAT scheduler in `scheduler.py`.
"""
from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.timeutil import add_minutes, ensure_utc, minutes_between, utcnow
from ..models import (
    Application,
    AppStatus,
    Drive,
    DriveStatus,
    Event,
    Panel,
    ProposalStatus,
    ReplanProposal,
    Round,
    RoundKind,
    Slot,
    SlotStatus,
)
from . import notifications
from .events import emit
from .scheduler import REPLAN_WEIGHTS, Candidate, PanelSpec, SchedulingError, schedule, validate
from .workhours import WorkingHours, drive_hours, local_date

ACTIVE_SLOT = (SlotStatus.scheduled, SlotStatus.in_progress)
BUFFER_BETWEEN_ROUNDS_MIN = 5
ARRIVAL_WINDOW_MIN = 15  # a not-yet-checked-in student is never pulled earlier than this before their slot
DOWN_HORIZON = timedelta(hours=12)  # how far a panel that is down "indefinitely" pushes its queue in projections


class LiveError(Exception):
    pass


def _ceil_min(origin: datetime, dt: datetime) -> int:
    return max(0, math.ceil(minutes_between(origin, dt)))


def _student_busy(db: Session, student_id: int, exclude_slot_ids: set[int], origin: datetime,
                  day_end: datetime) -> list[tuple[int, int]]:
    """Every other active slot of this student (any drive) that overlaps the planning window."""
    rows = db.scalars(
        select(Slot).join(Application, Slot.application_id == Application.id)
        .where(Application.student_id == student_id, Slot.status.in_(ACTIVE_SLOT))
    ).all()
    out = []
    for s in rows:
        if s.id in exclude_slot_ids:
            continue
        start, end = ensure_utc(s.start_at), ensure_utc(s.end_at)
        assert start and end
        if end <= origin or start >= day_end:
            continue
        out.append((max(0, math.floor(minutes_between(origin, start))) if start > origin else 0,
                    _ceil_min(origin, end) + BUFFER_BETWEEN_ROUNDS_MIN))
    return out


def round_candidates(db: Session, rnd: Round) -> list[Application]:
    return list(db.scalars(
        select(Application).where(Application.drive_id == rnd.drive_id,
                                  Application.status == AppStatus.in_process,
                                  Application.rounds_cleared == rnd.seq - 1)
        .order_by(Application.id)
    ).all())


# ---------------------------------------------------------------- first schedule of a round
@dataclass
class RoundPlan:
    round_id: int
    slots: int
    finish: datetime | None
    solve_ms: int
    optimal: bool
    days: list[dict[str, object]] | None = None   # [{"date": "2026-11-16", "slots": 12}, ...]
    hours: str = ""


def schedule_round(db: Session, drive: Drive, rnd: Round, start_at: datetime, actor: str,
                   hours: WorkingHours | None = None) -> RoundPlan:
    if rnd.kind != RoundKind.interview:
        raise LiveError("Only interview rounds are scheduled into panel slots; tests are bulk results.")
    down = {pid for pid, until in panel_holds(db, drive.id).items() if until is None}
    panels = [p for p in db.scalars(select(Panel).where(Panel.round_id == rnd.id).order_by(Panel.id)).all()
              if p.id not in down]
    if not panels:
        raise LiveError(f"Add at least one available panel to {rnd.name} before scheduling.")
    apps = round_candidates(db, rnd)
    if not apps:
        raise LiveError(f"No candidates are waiting for {rnd.name}. Shortlist candidates first.")

    # Drop not-yet-started slots of this round; started or finished ones are fixed facts.
    existing = db.scalars(select(Slot).where(Slot.round_id == rnd.id)).all()
    fixed_by_panel: dict[int, list[tuple[int, int]]] = {p.id: [] for p in panels}
    scheduled_apps: set[int] = set()
    for s in existing:
        if s.status == SlotStatus.scheduled:
            db.delete(s)
        elif s.status != SlotStatus.cancelled:
            scheduled_apps.add(s.application_id)
            if s.panel_id in fixed_by_panel and ensure_utc(s.end_at) > start_at:  # type: ignore[operator]
                fixed_by_panel[s.panel_id].append((0, _ceil_min(start_at, ensure_utc(s.end_at))))  # type: ignore[arg-type]
    db.flush()

    origin = ensure_utc(start_at)
    assert origin
    hours = hours or drive_hours(db, drive.id)
    working = hours.windows(origin)
    if not working:
        raise LiveError("No working time is left in the chosen days. Pick a later start or allow more days.")
    horizon_end = add_minutes(origin, max(we for _, we in working))
    cands = [
        Candidate(key=a.id, busy=tuple(_student_busy(db, a.student_id, set(), origin, horizon_end)))
        for a in apps if a.id not in scheduled_apps
    ]
    specs = [PanelSpec(id=p.id, blocked=tuple(fixed_by_panel[p.id])) for p in panels]
    try:
        result = schedule(cands, specs, rnd.duration_min, time_limit_sec=get_settings().solver_time_limit_sec,
                          working=working)
    except SchedulingError as exc:
        raise LiveError(str(exc)) from exc
    problems = validate(result.assignments, cands, specs, working)
    if problems:  # defensive: never persist a plan that breaks a hard constraint
        raise LiveError("Generated schedule failed validation: " + "; ".join(problems[:3]))

    for a in result.assignments:
        db.add(Slot(drive_id=drive.id, round_id=rnd.id, panel_id=a.panel_id, application_id=a.key,
                    start_at=add_minutes(origin, a.start), end_at=add_minutes(origin, a.end)))
    first = add_minutes(origin, result.assignments[0].start) if result.assignments else origin
    if drive.drive_start is None or first < ensure_utc(drive.drive_start):  # type: ignore[operator]
        drive.drive_start = first
    per_day: dict[str, int] = {}
    for a in result.assignments:
        key = local_date(add_minutes(origin, a.start), hours).isoformat()
        per_day[key] = per_day.get(key, 0) + 1
    days = [{"date": k, "slots": v} for k, v in sorted(per_day.items())]
    db.flush()
    emit(db, "schedule.generated", actor, entity="round", entity_id=rnd.id, drive_id=drive.id,
         payload={"slots": len(result.assignments), "solve_ms": result.solve_ms, "optimal": result.optimal,
                  "hours": hours.to_payload(), "days": days})
    apps_by_id = {a.id: a for a in apps}
    for a in result.assignments:
        app = apps_by_id[a.key]
        notifications.notify(db, [app.student_id], f"{drive.company.name}: {rnd.name} scheduled",
                             f"Your {rnd.name} slot is on your timeline with the time, room and panel.",
                             link="/student/schedule")
    finish = add_minutes(origin, result.makespan) if result.assignments else None
    return RoundPlan(rnd.id, len(result.assignments), finish, result.solve_ms, result.optimal, days,
                     hours.describe())


# ---------------------------------------------------------------- panel holds (breaks, overruns, panel down)
def panel_holds(db: Session, drive_id: int, now: datetime | None = None) -> dict[int, datetime | None]:
    """Panels that are currently unavailable, from the event log: panel id -> free again at (None = down).

    Holds live in the append-only event log rather than a column, so they are audited for free and need no
    schema migration. A later `panel.release` cancels a hold; timed holds lapse on their own.
    """
    now = now or utcnow()
    holds: dict[int, datetime | None] = {}
    rows = db.scalars(select(Event).where(Event.drive_id == drive_id, Event.type.in_(("panel.hold", "panel.release")))
                      .order_by(Event.id)).all()
    for ev in rows:
        pid = ev.entity_id
        if pid is None:
            continue
        if ev.type == "panel.release":
            holds.pop(pid, None)
            continue
        until = ev.payload.get("until") if ev.payload else None
        holds[pid] = ensure_utc(datetime.fromisoformat(until)) if until else None
    return {pid: u for pid, u in holds.items() if u is None or u > now}


def _hold_until(holds: dict[int, datetime | None], panel_id: int, now: datetime) -> datetime | None:
    if panel_id not in holds:
        return None
    until = holds[panel_id]
    return until if until is not None else now + DOWN_HORIZON


# ---------------------------------------------------------------- projections
@dataclass
class SlotProjection:
    slot: Slot
    projected_start: datetime
    projected_end: datetime
    lateness_min: int
    position: int  # 0 = in the room now


def project_panel(slots: Sequence[Slot], duration: int, now: datetime,
                  hold_until: datetime | None = None) -> list[SlotProjection]:
    """Project when each remaining slot on one panel will actually run, given overruns and holds so far.

    A hold (break, overrun, panel down) keeps the panel from starting anyone new until `hold_until`; an interview
    already in the room finishes normally.
    """
    active = sorted((s for s in slots if s.status in ACTIVE_SLOT),
                    key=lambda s: (s.status != SlotStatus.in_progress, ensure_utc(s.start_at)))
    t = now
    out: list[SlotProjection] = []
    for pos, s in enumerate(active):
        planned = ensure_utc(s.start_at)
        assert planned
        if s.status == SlotStatus.in_progress:
            started = ensure_utc(s.actual_start) or planned
            end = max(now, add_minutes(started, duration))
            out.append(SlotProjection(s, started, end, max(0, round(minutes_between(planned, started))), 0))
            t = end
            continue
        start = max(t, planned, hold_until or t)
        end = add_minutes(start, duration)
        out.append(SlotProjection(s, start, end, max(0, round(minutes_between(planned, start))), pos))
        t = end
    return out


def drive_projection(db: Session, drive: Drive, now: datetime | None = None) -> dict[int, list[SlotProjection]]:
    now = now or utcnow()
    holds = panel_holds(db, drive.id, now)
    result: dict[int, list[SlotProjection]] = {}
    for rnd in drive.rounds:
        if rnd.kind != RoundKind.interview:
            continue
        for panel in rnd.panels:
            slots = db.scalars(select(Slot).where(Slot.panel_id == panel.id)).all()
            result[panel.id] = project_panel(slots, rnd.duration_min, now, _hold_until(holds, panel.id, now))
    return result


# ---------------------------------------------------------------- re-planning
def build_replan(db: Session, drive: Drive, now: datetime) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Solve the remaining day from `now`. Returns (changes, metrics) without writing anything."""
    changes: list[dict[str, object]] = []
    before_late = 0
    after_late = 0
    before_finish = now
    after_finish = now
    projections = drive_projection(db, drive, now)
    holds = panel_holds(db, drive.id, now)
    # One spare day, so a disruption on the last day becomes a next-morning proposal instead of no proposal.
    working = drive_hours(db, drive.id).windows(now, extra_days=1)
    for rnd in drive.rounds:
        if rnd.kind != RoundKind.interview or not rnd.panels:
            continue
        panel_ids = [p.id for p in rnd.panels]
        remaining: list[Slot] = []
        specs: list[PanelSpec] = []
        for p_id in panel_ids:
            proj = projections.get(p_id, [])
            free = now
            for sp in proj:
                before_late = max(before_late, sp.lateness_min)
                before_finish = max(before_finish, sp.projected_end)
                if sp.slot.status == SlotStatus.in_progress:
                    free = sp.projected_end
                else:
                    remaining.append(sp.slot)
            if p_id in holds and holds[p_id] is None:
                continue  # panel is down: it takes no one, its queue moves to the other panels
            free = max(free, holds.get(p_id) or free)
            specs.append(PanelSpec(id=p_id, available_from=_ceil_min(now, free)))
        if not remaining or not specs:
            continue
        slot_ids = {s.id for s in remaining}
        day_end = add_minutes(now, max((we for _, we in working), default=16 * 60))
        cands = []
        for s in remaining:
            planned = ensure_utc(s.start_at)
            assert planned
            earliest = now if s.checked_in_at else max(now, planned - timedelta(minutes=ARRIVAL_WINDOW_MIN))
            cands.append(Candidate(
                key=s.id,
                earliest=_ceil_min(now, earliest),
                busy=tuple(_student_busy(db, s.application.student_id, slot_ids, now, day_end)),
                preferred_panel=s.panel_id,
                preferred_start=_ceil_min(now, planned),
                priority=1 if s.checked_in_at else 0,
            ))
        try:
            result = schedule(cands, specs, rnd.duration_min, weights=REPLAN_WEIGHTS,
                              time_limit_sec=get_settings().solver_time_limit_sec, working=working or None)
        except SchedulingError:
            continue
        by_slot = {s.id: s for s in remaining}
        for a in result.assignments:
            s = by_slot[a.key]
            new_start = add_minutes(now, a.start)
            old_start = ensure_utc(s.start_at)
            assert old_start
            after_late = max(after_late, max(0, round(minutes_between(old_start, new_start))))
            after_finish = max(after_finish, add_minutes(now, a.end))
            if a.panel_id != s.panel_id or abs(minutes_between(old_start, new_start)) >= 3:
                changes.append({
                    "slot_id": s.id, "application_id": s.application_id,
                    "student": s.application.student.name, "round": rnd.name,
                    "from_panel": s.panel.name, "to_panel": next(p.name for p in rnd.panels if p.id == a.panel_id),
                    "to_panel_id": a.panel_id,
                    "from_start": old_start.isoformat(), "to_start": new_start.isoformat(),
                    "to_end": add_minutes(now, a.end).isoformat(),
                })
    metrics = {
        "max_lateness_before_min": before_late, "max_lateness_after_min": after_late,
        "finish_before": before_finish.isoformat(), "finish_after": after_finish.isoformat(),
        "saved_min": max(0, round(minutes_between(after_finish, before_finish))),
    }
    return changes, metrics


def propose_replan(db: Session, drive: Drive, reason: str, actor: str,
                   now: datetime | None = None) -> ReplanProposal | None:
    now = now or utcnow()
    changes, metrics = build_replan(db, drive, now)
    if not changes:
        return None
    for old in db.scalars(select(ReplanProposal).where(ReplanProposal.drive_id == drive.id,
                                                       ReplanProposal.status == ProposalStatus.pending)):
        old.status = ProposalStatus.superseded
    proposal = ReplanProposal(drive_id=drive.id, reason=reason, changes=changes, metrics=metrics)
    db.add(proposal)
    db.flush()
    emit(db, "replan.proposed", actor, entity="proposal", entity_id=proposal.id, drive_id=drive.id,
         payload={"reason": reason, "changes": len(changes)})
    return proposal


def apply_proposal(db: Session, proposal: ReplanProposal, actor: str) -> int:
    if proposal.status != ProposalStatus.pending:
        raise LiveError("This proposal is no longer pending.")
    applied = 0
    touched_students: list[int] = []
    for ch in proposal.changes:
        slot = db.get(Slot, int(ch["slot_id"]))  # type: ignore[call-overload]
        if slot is None or slot.status != SlotStatus.scheduled:
            continue  # the slot started or changed since the proposal; skip it safely
        slot.panel_id = int(ch["to_panel_id"])  # type: ignore[call-overload]
        slot.start_at = datetime.fromisoformat(str(ch["to_start"]))
        slot.end_at = datetime.fromisoformat(str(ch["to_end"]))
        touched_students.append(slot.application.student_id)
        applied += 1
    proposal.status = ProposalStatus.approved
    proposal.decided_by = actor
    proposal.decided_at = utcnow()
    db.flush()
    emit(db, "replan.approved", actor, entity="proposal", entity_id=proposal.id, drive_id=proposal.drive_id,
         payload={"applied": applied})
    if touched_students:
        notifications.notify(db, touched_students, "Your interview time changed",
                             "The drive was re-planned to keep things on time. Open your timeline for the new "
                             "time and panel.", link="/student/schedule")
    return applied


# ---------------------------------------------------------------- slot state changes
def _slot_round(slot: Slot) -> Round:
    return slot.round


def check_in(db: Session, slot: Slot, actor: str) -> Slot:
    if slot.status not in (SlotStatus.scheduled, SlotStatus.no_show):
        raise LiveError("This slot is already in progress or finished.")
    late_arrival = slot.status == SlotStatus.no_show
    slot.status = SlotStatus.scheduled
    slot.checked_in_at = utcnow()
    db.flush()
    emit(db, "slot.checked_in", actor, entity="slot", entity_id=slot.id, drive_id=slot.drive_id,
         student_id=slot.application.student_id)
    if late_arrival:
        propose_replan(db, slot.application.drive, f"{slot.application.student.name} arrived after being "
                       "marked absent", "system")
    return slot


def start_slot(db: Session, slot: Slot, actor: str) -> Slot:
    if slot.status != SlotStatus.scheduled:
        raise LiveError("Only a scheduled slot can be started.")
    busy = db.scalar(select(Slot).where(Slot.panel_id == slot.panel_id, Slot.status == SlotStatus.in_progress))
    if busy is not None:
        raise LiveError("This panel already has an interview in progress. Finish it first.")
    slot.status = SlotStatus.in_progress
    slot.actual_start = utcnow()
    slot.checked_in_at = slot.checked_in_at or slot.actual_start
    if slot.application.drive.status in (DriveStatus.open, DriveStatus.closed):
        slot.application.drive.status = DriveStatus.live
    db.flush()
    emit(db, "slot.started", actor, entity="slot", entity_id=slot.id, drive_id=slot.drive_id,
         student_id=slot.application.student_id)
    return slot


def complete_slot(db: Session, slot: Slot, result: str, actor: str) -> Slot:
    if slot.status != SlotStatus.in_progress:
        raise LiveError("Start the interview before marking it done.")
    if result not in ("pass", "fail"):
        raise LiveError("Result must be pass or fail.")
    slot.status = SlotStatus.done
    slot.actual_end = utcnow()
    slot.result = result
    app = slot.application
    rnd = _slot_round(slot)
    if result == "pass":
        app.rounds_cleared = max(app.rounds_cleared, rnd.seq)
        last_seq = max(r.seq for r in app.drive.rounds)
        if rnd.seq >= last_seq:
            app.status = AppStatus.selected
    else:
        app.status = AppStatus.rejected
        app.rejected_round = rnd.seq
    db.flush()
    emit(db, "slot.completed", actor, entity="slot", entity_id=slot.id, drive_id=slot.drive_id,
         student_id=app.student_id, payload={"result": result, "round": rnd.name})
    if result == "pass" and app.status == AppStatus.in_process:
        place_in_next_round(db, app, actor)
    maybe_propose_for_delay(db, app.drive, actor)
    return slot


def mark_no_show(db: Session, slot: Slot, actor: str) -> Slot:
    if slot.status != SlotStatus.scheduled:
        raise LiveError("Only a waiting slot can be marked as a no-show.")
    slot.status = SlotStatus.no_show
    db.flush()
    emit(db, "slot.no_show", actor, entity="slot", entity_id=slot.id, drive_id=slot.drive_id,
         student_id=slot.application.student_id)
    propose_replan(db, slot.application.drive, f"{slot.application.student.name} did not show up", "system")
    return slot


def place_in_next_round(db: Session, app: Application, actor: str) -> Slot | None:
    """A candidate who clears a round joins the next interview round's queue automatically."""
    nxt = next((r for r in app.drive.rounds if r.seq == app.rounds_cleared + 1), None)
    if nxt is None or nxt.kind != RoundKind.interview or not nxt.panels:
        return None
    if db.scalar(select(Slot).where(Slot.round_id == nxt.id, Slot.application_id == app.id)):
        return None
    if not db.scalar(select(Slot.id).where(Slot.round_id == nxt.id).limit(1)):
        return None  # next round not scheduled yet; the cell will schedule it as a batch
    now = utcnow()
    projections = drive_projection(db, app.drive, now)
    holds = panel_holds(db, app.drive_id, now)
    specs = []
    for p in nxt.panels:
        if p.id in holds and holds[p.id] is None:
            continue
        proj = projections.get(p.id, [])
        blocked = tuple((_ceil_min(now, sp.projected_start), _ceil_min(now, sp.projected_end)) for sp in proj)
        until = holds.get(p.id)
        if until is not None:
            blocked += ((0, _ceil_min(now, until)),)
        specs.append(PanelSpec(id=p.id, blocked=blocked))
    if not specs:
        return None
    working = drive_hours(db, app.drive_id).windows(now, extra_days=1)
    horizon_end = add_minutes(now, max((we for _, we in working), default=16 * 60))
    cand = Candidate(key=app.id, earliest=BUFFER_BETWEEN_ROUNDS_MIN,
                     busy=tuple(_student_busy(db, app.student_id, set(), now, horizon_end)))
    try:
        result = schedule([cand], specs, nxt.duration_min, time_limit_sec=2.0, working=working or None)
    except SchedulingError:
        return None
    a = result.assignments[0]
    slot = Slot(drive_id=app.drive_id, round_id=nxt.id, panel_id=a.panel_id, application_id=app.id,
                start_at=add_minutes(now, a.start), end_at=add_minutes(now, a.end), checked_in_at=now)
    db.add(slot)
    db.flush()
    emit(db, "slot.added", actor, entity="slot", entity_id=slot.id, drive_id=app.drive_id,
         student_id=app.student_id, payload={"round": nxt.name})
    notifications.notify(db, [app.student_id], f"You cleared the round. Next: {nxt.name}",
                         f"You're in the {nxt.name} queue. Your estimated time is on your dashboard.",
                         link="/student")
    return slot


def maybe_propose_for_delay(db: Session, drive: Drive, actor: str, now: datetime | None = None) -> None:
    now = now or utcnow()
    threshold = get_settings().delay_threshold_min
    worst = max((sp.lateness_min for proj in drive_projection(db, drive, now).values() for sp in proj), default=0)
    if worst < threshold:
        return
    pending = db.scalar(select(ReplanProposal).where(ReplanProposal.drive_id == drive.id,
                                                     ReplanProposal.status == ProposalStatus.pending))
    if pending is not None and minutes_between(ensure_utc(pending.created_at), now) < 5:  # type: ignore[arg-type]
        return
    propose_replan(db, drive, f"Running {worst} min behind schedule", actor, now)


def monitor_tick(db: Session, now: datetime | None = None) -> None:
    """Background check for live drives: overruns and students who haven't arrived."""
    now = now or utcnow()
    grace = timedelta(minutes=get_settings().no_show_grace_min)
    drives = db.scalars(select(Drive).where(Drive.status == DriveStatus.live)).all()
    for drive in drives:
        late_arrivals = db.scalars(
            select(Slot).where(Slot.drive_id == drive.id, Slot.status == SlotStatus.scheduled,
                               Slot.checked_in_at.is_(None), Slot.start_at < now - grace)
        ).all()
        for slot in late_arrivals:
            already = db.scalar(select(ReplanProposal.id).where(
                ReplanProposal.drive_id == drive.id, ReplanProposal.status == ProposalStatus.pending))
            if already is None:
                propose_replan(db, drive, f"{slot.application.student.name} hasn't checked in", "monitor", now)
                break
        maybe_propose_for_delay(db, drive, "monitor", now)


# ---------------------------------------------------------------- disruptions (live "what if it goes wrong")
DISRUPTIONS = ("overrun", "panel_down", "no_show", "release")


def hold_panel(db: Session, drive: Drive, panel: Panel, minutes: int | None, reason: str, actor: str) -> None:
    until = utcnow() + timedelta(minutes=minutes) if minutes else None
    emit(db, "panel.hold", actor, entity="panel", entity_id=panel.id, drive_id=drive.id,
         payload={"until": until.isoformat() if until else None, "reason": reason, "panel": panel.name})


def release_panel(db: Session, drive: Drive, panel: Panel, actor: str) -> None:
    emit(db, "panel.release", actor, entity="panel", entity_id=panel.id, drive_id=drive.id,
         payload={"panel": panel.name})


def _next_waiting(db: Session, drive: Drive, panel_id: int | None) -> Slot | None:
    q = select(Slot).where(Slot.drive_id == drive.id, Slot.status == SlotStatus.scheduled)
    if panel_id is not None:
        q = q.where(Slot.panel_id == panel_id)
    slots = db.scalars(q.order_by(Slot.start_at)).all()
    return next((s for s in slots if s.checked_in_at is None), slots[0] if slots else None)


def disrupt(db: Session, drive: Drive, kind: str, actor: str, *, panel_id: int | None = None,
            minutes: int = 30, auto_apply: bool = True) -> dict[str, object]:
    """Inject a real-world disruption, re-plan the rest of the day with CP-SAT and (optionally) apply it.

    Returns what happened, what the solver changed, how long it took, and before/after metrics, so the control
    room can show the whole story in one response. Students and recruiters see the new plan over WebSockets.
    """
    if kind not in DISRUPTIONS:
        raise LiveError(f"Unknown disruption. Use one of: {', '.join(DISRUPTIONS)}.")
    panel = None
    if panel_id is not None:
        panel = db.get(Panel, panel_id)
        if panel is None or panel.drive_id != drive.id:
            raise LiveError("That panel isn't part of this drive.")
    if kind in ("overrun", "panel_down", "release") and panel is None:
        raise LiveError("Pick a panel.")

    if kind == "overrun":
        assert panel is not None
        minutes = max(5, min(int(minutes), 180))
        hold_panel(db, drive, panel, minutes, f"{panel.name} running {minutes} min over", actor)
        what = f"{panel.name} is running {minutes} min over"
    elif kind == "panel_down":
        assert panel is not None
        hold_panel(db, drive, panel, None, f"{panel.name} unavailable", actor)
        what = f"{panel.name} ({panel.room}) is unavailable for the rest of the day"
    elif kind == "release":
        assert panel is not None
        release_panel(db, drive, panel, actor)
        what = f"{panel.name} is available again"
    else:
        slot = _next_waiting(db, drive, panel_id)
        if slot is None:
            raise LiveError("No one is waiting to be marked absent.")
        slot.status = SlotStatus.no_show
        db.flush()
        emit(db, "slot.no_show", actor, entity="slot", entity_id=slot.id, drive_id=drive.id,
             student_id=slot.application.student_id)
        what = f"{slot.application.student.name} didn't show up for {slot.panel.name}"
    db.flush()

    started = time.perf_counter()
    proposal = propose_replan(db, drive, what, actor)
    solve_ms = round((time.perf_counter() - started) * 1000)
    out: dict[str, object] = {"disruption": what, "kind": kind, "solve_ms": solve_ms, "proposal": None,
                              "changes": [], "metrics": None, "applied": 0}
    if proposal is None:
        return out
    out.update({"proposal": proposal.id, "changes": proposal.changes, "metrics": proposal.metrics})
    if auto_apply:
        out["applied"] = apply_proposal(db, proposal, actor)
    return out
