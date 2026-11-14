"""Decision-driven dashboards. Every number here is derived from operational data; nothing is entered twice."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.timeutil import ensure_utc, utcnow
from ..models import (
    Application,
    AppStatus,
    Dispute,
    DisputeStatus,
    Drive,
    DriveStatus,
    EligibilityDecision,
    Notification,
    Offer,
    OfferStatus,
    ProposalStatus,
    ReplanProposal,
    Resume,
    RoundKind,
    Slot,
    SlotStatus,
    Student,
)
from .eligibility import StudentProfile, evaluate, rule_specs
from .live import drive_projection
from .policy import Placement, active_policy, placement_for, placements_for_all

UPCOMING = (DriveStatus.open, DriveStatus.draft)
FUNNEL_STAGES = ("Applied", "Shortlisted", "Test cleared", "Interview cleared", "Offer")


@dataclass
class FunnelCounts:
    applied: int = 0
    shortlisted: int = 0
    test_reached: int = 0
    test_cleared: int = 0
    interview_reached: int = 0
    interview_cleared: int = 0
    offers: int = 0


def application_funnel(apps: list[Application]) -> FunnelCounts:
    f = FunnelCounts()
    for app in apps:
        if app.status == AppStatus.withdrawn:
            continue
        f.applied += 1
        shortlisted = app.status in (AppStatus.in_process, AppStatus.selected) or app.rejected_round is not None
        f.shortlisted += shortlisted
        if not shortlisted:
            continue
        reached_seq = app.rejected_round if app.rejected_round is not None else app.rounds_cleared + 1
        for kind, reached_attr, cleared_attr in ((RoundKind.test, "test_reached", "test_cleared"),
                                                 (RoundKind.interview, "interview_reached", "interview_cleared")):
            seqs = [r.seq for r in app.drive.rounds if r.kind == kind]
            if seqs and min(seqs) <= reached_seq:
                setattr(f, reached_attr, getattr(f, reached_attr) + 1)
                if all(s <= app.rounds_cleared for s in seqs):
                    setattr(f, cleared_attr, getattr(f, cleared_attr) + 1)
        f.offers += app.offer is not None and app.offer.status != OfferStatus.revoked
    return f


def funnel_insight(f: FunnelCounts) -> str:
    if f.applied == 0:
        return "You haven't applied to a drive yet. Start with the drives you're eligible for."
    if f.shortlisted == 0:
        return f"None of your {f.applied} applications were shortlisted yet. Tailor your resume to each JD's skills."
    if f.interview_reached >= 2 and f.interview_cleared == 0 and f.test_cleared >= 1:
        return (f"You've cleared {f.test_cleared} of {f.test_reached} tests but none of {f.interview_reached} "
                "interview stages. Put your time into mock interviews, not more test practice.")
    if f.test_reached >= 2 and f.test_cleared / f.test_reached < 0.5:
        return (f"You've cleared {f.test_cleared} of {f.test_reached} online tests; that's where most "
                "applications stop. Timed practice tests are your best lever.")
    if f.interview_reached and f.interview_cleared / f.interview_reached < 0.5:
        return (f"You've cleared {f.interview_cleared} of {f.interview_reached} interview stages. "
                "Ask the cell for a mock interview before your next drive.")
    return "Your pipeline is healthy. Keep applying to drives you're eligible for."


def _drive_label(d: Drive) -> str:
    return f"{d.company.name} ({d.role})"


# ---------------------------------------------------------------- student
def student_dashboard(db: Session, student: Student, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    _, cfg = active_policy(db)
    placement = placement_for(db, student.id)
    apps = list(db.scalars(select(Application).where(Application.student_id == student.id)).all())
    applied_drive_ids = {a.drive_id for a in apps}
    decisions = {d.drive_id: d for d in db.scalars(
        select(EligibilityDecision).where(EligibilityDecision.student_id == student.id))}
    upcoming = list(db.scalars(select(Drive).where(Drive.status.in_(UPCOMING))).all())
    open_drives = [d for d in upcoming if d.status == DriveStatus.open]

    # Action feed -----------------------------------------------------------
    actions: list[dict[str, Any]] = []
    for app in apps:
        if app.offer and app.offer.status == OfferStatus.pending:
            actions.append({"kind": "offer", "title": f"Decide on your {app.drive.company.name} offer",
                            "detail": f"{app.offer.ctc_lpa:g} LPA. See what accepting changes before you decide.",
                            "due": ensure_utc(app.offer.deadline), "link": "/student/offers"})
    my_slots = db.scalars(
        select(Slot).join(Application, Slot.application_id == Application.id)
        .where(Application.student_id == student.id, Slot.status.in_((SlotStatus.scheduled, SlotStatus.in_progress)))
    ).all()
    for s in my_slots:
        start = ensure_utc(s.start_at)
        assert start
        # only interviews that are still ahead (or just started), never stale slots from an earlier day
        if -timedelta(hours=2) < start - now < timedelta(hours=36) or s.status == SlotStatus.in_progress:
            actions.append({"kind": "interview", "title": f"{s.round.name} with {s.application.drive.company.name}",
                            "detail": f"{s.panel.name}, {s.panel.room}. Check in when you reach the venue.",
                            "due": start, "link": "/student/schedule"})
    for d in open_drives:
        dec = decisions.get(d.id)
        if d.id in applied_drive_ids or dec is None or not dec.eligible or d.apply_deadline is None:
            continue
        deadline = ensure_utc(d.apply_deadline)
        assert deadline
        if now < deadline < now + timedelta(hours=72):
            actions.append({"kind": "apply", "title": f"Apply to {_drive_label(d)}",
                            "detail": "You're eligible and applications close soon.", "due": deadline,
                            "link": f"/student/drives/{d.id}"})
    if not db.scalar(select(func.count()).select_from(Resume).where(Resume.student_id == student.id)):
        actions.append({"kind": "resume", "title": "Upload your resume",
                        "detail": "You need at least one resume to apply.", "due": None, "link": "/student/profile"})
    unread = db.scalar(select(func.count()).select_from(Notification).where(
        Notification.student_id == student.id, Notification.read.is_(False))) or 0
    far = now + timedelta(days=3650)
    actions.sort(key=lambda a: a["due"] or far)
    for a in actions:
        a["due"] = a["due"].isoformat() if a["due"] else None

    # Funnel ----------------------------------------------------------------
    f = application_funnel(apps)
    funnel = [{"stage": name, "count": c} for name, c in zip(
        FUNNEL_STAGES, (f.applied, f.shortlisted, f.test_cleared, f.interview_cleared, f.offers), strict=True)]

    # Skill gap -------------------------------------------------------------
    eligible_drives = [d for d in upcoming if decisions.get(d.id) and decisions[d.id].eligible]
    have = {s.lower() for s in student.skills}
    demand = Counter(skill for d in eligible_drives for skill in {s.lower() for s in d.skills})
    gaps = [{"skill": k, "drives": v, "of": len(eligible_drives)} for k, v in demand.most_common() if k not in have][:6]
    strengths = [{"skill": k, "drives": v} for k, v in demand.most_common() if k in have][:6]

    # Opportunity map -------------------------------------------------------
    profile = StudentProfile.of(student)
    eligible_now = [{"id": d.id, "label": _drive_label(d), "tier": cfg.tier_name(d.ctc_lpa)}
                    for d in open_drives if decisions.get(d.id) and decisions[d.id].eligible]
    unlockable: dict[int, list[str]] = defaultdict(list)
    for d in upcoming:
        dec = decisions.get(d.id)
        if dec is None or dec.eligible:
            continue
        failed = [r for r in dec.reasons if not r["ok"]]
        if failed and all(r.get("field") == "backlogs" for r in failed):
            limit = next((int(r.value) for r in d.rules if r.field == "backlogs" and r.approved), 0)
            unlockable[max(1, student.backlogs - limit)].append(_drive_label(d))
    lost_if_accept = []
    for app in apps:
        if not (app.offer and app.offer.status == OfferStatus.pending):
            continue
        hypothetical = placement.with_offer(app.offer.ctc_lpa, app.drive.company.name, app.drive.company.sector)
        lost = [_drive_label(d) for d in upcoming
                if d.id != app.drive_id and decisions.get(d.id) and decisions[d.id].eligible
                and not evaluate(profile, rule_specs(d.rules), cfg, hypothetical, d.ctc_lpa, d.id).eligible]
        lost_if_accept.append({"offer_id": app.offer.id, "company": app.drive.company.name,
                               "tier": cfg.tier_name(app.offer.ctc_lpa), "lost": lost})

    # Drive-day mode --------------------------------------------------------
    live_items = []
    for s in my_slots:
        start = ensure_utc(s.start_at)
        assert start
        # the drive-day banner covers today's interviews and the next day's (up to 36 h ahead); a slot that
        # started over 12 h ago is stale unless the panel is still running it
        ahead_s = (start - now).total_seconds()
        if s.status != SlotStatus.in_progress and not (-12 * 3600 <= ahead_s <= 36 * 3600):
            continue
        proj = drive_projection(db, s.application.drive, now).get(s.panel_id, [])
        mine = next((p for p in proj if p.slot.id == s.id), None)
        live_items.append({
            "slot_id": s.id, "company": s.application.drive.company.name, "round": s.round.name,
            "panel": s.panel.name, "room": s.panel.room, "status": s.status.value,
            "checked_in": s.checked_in_at is not None, "planned_start": start.isoformat(),
            "eta": mine.projected_start.isoformat() if mine else start.isoformat(),
            "position": mine.position if mine else None,
            "ahead": sum(1 for p in proj if p.position < (mine.position if mine else 0)),
        })
    live_items.sort(key=lambda x: x["eta"])

    placed = placement.best_ctc is not None
    frozen = placed and placement.frozen(cfg)
    upgrade = None
    if placed and not frozen:
        used = placement.attempts_used()
        upgrade = {"threshold_lpa": cfg.freeze_at_ctc_lpa, "total": cfg.upgrade_attempts,
                   "used": used, "left": max(0, cfg.upgrade_attempts - used)}
    return {
        "student": {"name": student.name, "roll_no": student.roll_no, "branch": student.branch,
                    "programme": student.programme},
        "placement": {"placed": placed, "tier": cfg.tier_name(placement.best_ctc) if placed else None,
                      "companies": list(placement.companies), "best_ctc_lpa": placement.best_ctc,
                      "season_over": frozen, "upgrade": upgrade},
        "actions": actions,
        "unread": unread,
        "funnel": funnel,
        "insight": funnel_insight(f),
        "skills": {"gaps": gaps, "strengths": strengths, "eligible_drives": len(eligible_drives)},
        "opportunity": {
            "eligible_now": eligible_now,
            "unlockable": [{"clear_backlogs": k, "drives": v} for k, v in sorted(unlockable.items())],
            "lost_if_accept": lost_if_accept,
        },
        "live": live_items,
    }


# ---------------------------------------------------------------- placement cell
def exceptions_inbox(db: Session, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or utcnow()
    items: list[dict[str, Any]] = []
    for d in db.scalars(select(Dispute).where(Dispute.status == DisputeStatus.open).order_by(Dispute.created_at)):
        items.append({"kind": "dispute", "id": d.id, "drive_id": d.drive_id, "severity": "medium",
                      "title": f"{d.student.name} disputes eligibility for {_drive_label(d.drive)}",
                      "detail": d.message, "at": ensure_utc(d.created_at).isoformat()})  # type: ignore[union-attr]
    for p in db.scalars(select(ReplanProposal).where(ReplanProposal.status == ProposalStatus.pending)):
        drive = db.get(Drive, p.drive_id)
        assert drive
        items.append({"kind": "replan", "id": p.id, "drive_id": p.drive_id, "severity": "high",
                      "title": f"Re-plan ready for {_drive_label(drive)}",
                      "detail": f"{p.reason}. Moves {len(p.changes)} candidate(s); saves "
                                f"{p.metrics.get('saved_min', 0)} min.",
                      "at": ensure_utc(p.created_at).isoformat()})  # type: ignore[union-attr]
    for drive in db.scalars(select(Drive).where(Drive.status.in_((DriveStatus.draft, DriveStatus.open)))):
        unsure = [r for r in drive.rules if not r.approved]
        if unsure:
            low = [r for r in unsure if r.confidence < 0.75]
            items.append({"kind": "rules", "id": drive.id, "drive_id": drive.id,
                          "severity": "medium" if low else "low",
                          "title": f"Approve eligibility rules for {_drive_label(drive)}",
                          "detail": f"{len(unsure)} rule(s) waiting"
                                    + (f", {len(low)} with low confidence" if low else "") + ".",
                          "at": ensure_utc(drive.updated_at).isoformat()})  # type: ignore[union-attr]
    grace = timedelta(minutes=get_settings().no_show_grace_min)
    late = db.scalars(select(Slot).where(Slot.status == SlotStatus.scheduled, Slot.checked_in_at.is_(None),
                                         Slot.start_at < now - grace, Slot.start_at > now - timedelta(hours=8)))
    for s in late:
        items.append({"kind": "absent", "id": s.id, "drive_id": s.drive_id, "severity": "high",
                      "title": f"{s.application.student.name} hasn't checked in",
                      "detail": f"{s.round.name}, {s.panel.name}. Mark as no-show to re-plan, or check them in.",
                      "at": ensure_utc(s.start_at).isoformat()})  # type: ignore[union-attr]
    for drive in db.scalars(select(Drive).where(Drive.status.in_((DriveStatus.open, DriveStatus.closed)))):
        deadline = ensure_utc(drive.apply_deadline)
        if deadline is None or now - deadline < timedelta(days=2):
            continue
        waiting = sum(1 for a in drive.applications if a.status == AppStatus.applied)
        if waiting:
            items.append({"kind": "followup", "id": drive.id, "drive_id": drive.id, "severity": "low",
                          "title": f"{drive.company.name} hasn't shortlisted yet",
                          "detail": f"{waiting} applicants waiting for {(now - deadline).days} days since the "
                                    "deadline.", "at": deadline.isoformat()})
    rank = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda i: (rank[i["severity"]], i["at"]))
    return items


def coverage_gaps(db: Session) -> dict[str, Any]:
    placements = placements_for_all(db)
    students = db.scalars(select(Student).where(Student.archived.is_(False))).all()
    upcoming_ids = list(db.scalars(select(Drive.id).where(Drive.status.in_(UPCOMING))))
    eligible_counts: Counter[int] = Counter()
    if upcoming_ids:
        for sid, in db.execute(select(EligibilityDecision.student_id).where(
                EligibilityDecision.drive_id.in_(upcoming_ids), EligibilityDecision.eligible.is_(True))):
            eligible_counts[sid] += 1
    unplaced = [s for s in students if s.id not in placements]
    uncovered = [s for s in unplaced if eligible_counts[s.id] == 0]
    by_branch = Counter(s.branch for s in uncovered)
    return {
        "unplaced": len(unplaced),
        "uncovered": len(uncovered),
        "by_branch": [{"branch": b, "students": n} for b, n in by_branch.most_common()],
        "students": [{"id": s.id, "name": s.name, "branch": s.branch, "cgpa": s.cgpa, "backlogs": s.backlogs}
                     for s in sorted(uncovered, key=lambda s: (s.branch, -s.cgpa))[:100]],
    }


def at_risk_students(db: Session) -> list[dict[str, Any]]:
    placements = placements_for_all(db)
    out = []
    finished = (DriveStatus.closed, DriveStatus.live, DriveStatus.completed)
    for s in db.scalars(select(Student).where(Student.archived.is_(False))):
        if s.id in placements:
            continue
        apps = [a for a in s.applications if a.status != AppStatus.withdrawn]
        f = application_funnel(apps)
        interview_losses = f.interview_reached - f.interview_cleared
        test_losses = f.test_reached - f.test_cleared
        eligible_missed = db.scalar(
            select(func.count()).select_from(EligibilityDecision).join(Drive, Drive.id == EligibilityDecision.drive_id)
            .where(EligibilityDecision.student_id == s.id, EligibilityDecision.eligible.is_(True),
                   Drive.status.in_(finished))) or 0
        reason = None
        if interview_losses >= 2:
            reason = f"Dropped at the interview stage {interview_losses} times"
        elif test_losses >= 3:
            reason = f"Dropped at the online test {test_losses} times"
        elif not apps and eligible_missed >= 3:
            reason = f"Hasn't applied to any of {eligible_missed} eligible drives"
        if reason:
            out.append({"id": s.id, "name": s.name, "branch": s.branch, "cgpa": s.cgpa, "reason": reason,
                        "suggestion": "Mock interview" if "interview" in reason else
                        "Test practice" if "test" in reason else "Counselling check-in"})
    return out


def placement_stats(db: Session) -> dict[str, Any]:
    _, cfg = active_policy(db)
    placements = placements_for_all(db)
    students = db.scalars(select(Student).where(Student.archived.is_(False))).all()
    total = len(students)
    by_branch: dict[str, dict[str, int]] = defaultdict(lambda: {"students": 0, "placed": 0})
    for s in students:
        by_branch[s.branch]["students"] += 1
        by_branch[s.branch]["placed"] += s.id in placements
    tiers = Counter(cfg.tier_name(p.best_ctc) for p in placements.values() if p.best_ctc is not None)
    offers = db.scalar(select(func.count()).select_from(Offer)) or 0
    return {
        "students": total, "placed": len(placements),
        "placed_pct": round(100 * len(placements) / total, 1) if total else 0.0,
        "offers": offers,
        "active_drives": db.scalar(select(func.count()).select_from(Drive).where(
            Drive.status.in_((DriveStatus.open, DriveStatus.closed, DriveStatus.live)))) or 0,
        "by_branch": [{"branch": b, **v} for b, v in sorted(by_branch.items())],
        "by_tier": [{"tier": t.name, "placed": tiers.get(t.name, 0)} for t in cfg.tiers],
    }


# ---------------------------------------------------------------- recruiter
def recruiter_overview(db: Session, drive: Drive, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    apps = [a for a in drive.applications if a.status != AppStatus.withdrawn]
    f = application_funnel(apps)
    remaining = sum(1 for a in apps if a.status in (AppStatus.in_process, AppStatus.selected))
    pending: list[dict[str, Any]] = []
    unapproved = [r for r in drive.rules if not r.approved]
    if unapproved:
        pending.append({"kind": "criteria", "title": "Placement cell is reviewing your criteria",
                        "detail": f"{len(unapproved)} rule(s) pending approval.", "mine": False})
    if any(a.status == AppStatus.applied for a in apps) and drive.status != DriveStatus.open:
        pending.append({"kind": "shortlist", "title": "Upload your screening shortlist",
                        "detail": f"{sum(a.status == AppStatus.applied for a in apps)} applicants are waiting.",
                        "mine": True})
    for rnd in drive.rounds:
        if rnd.kind == RoundKind.test:
            waiting = sum(1 for a in apps if a.status == AppStatus.in_process and a.rounds_cleared == rnd.seq - 1)
            if waiting:
                pending.append({"kind": "results", "title": f"Upload {rnd.name} results",
                                "detail": f"{waiting} candidates are waiting for results.", "mine": True,
                                "round_id": rnd.id})
        elif not rnd.panels:
            pending.append({"kind": "panels", "title": f"Confirm panels for {rnd.name}",
                            "detail": "The placement cell needs panel names and rooms to schedule.", "mine": True})
    selected_no_offer = [a for a in apps if a.status == AppStatus.selected and a.offer is None]
    if selected_no_offer:
        pending.append({"kind": "offers", "title": "Release offers",
                        "detail": f"{len(selected_no_offer)} selected candidate(s) don't have an offer yet.",
                        "mine": True})
    panels = []
    projection = drive_projection(db, drive, now)
    for rnd in drive.rounds:
        for p in rnd.panels:
            proj = projection.get(p.id, [])
            done = db.scalars(select(Slot).where(Slot.panel_id == p.id, Slot.status == SlotStatus.done)).all()
            durations = [
                (ensure_utc(s.actual_end) - ensure_utc(s.actual_start)).total_seconds() / 60  # type: ignore[operator]
                for s in done if s.actual_start and s.actual_end
            ]
            panels.append({
                "panel_id": p.id, "name": p.name, "room": p.room, "round": rnd.name,
                "remaining": len(proj), "done": len(done),
                "avg_min": round(sum(durations) / len(durations), 1) if durations else None,
                "planned_min": rnd.duration_min,
                "finish": max((sp.projected_end for sp in proj), default=None),
                "late_min": max((sp.lateness_min for sp in proj), default=0),
            })
    for p in panels:
        p["finish"] = p["finish"].isoformat() if p["finish"] else None
    offers = [a.offer for a in apps if a.offer is not None]
    return {
        "drive": {"id": drive.id, "company": drive.company.name, "role": drive.role, "status": drive.status.value,
                  "ctc_lpa": drive.ctc_lpa, "target_hires": drive.target_hires},
        "funnel": [{"stage": n, "count": c} for n, c in zip(
            FUNNEL_STAGES, (f.applied, f.shortlisted, f.test_cleared, f.interview_cleared, f.offers), strict=True)],
        "target": {"hires": drive.target_hires, "remaining_candidates": remaining,
                   "on_track": remaining >= drive.target_hires},
        "pending": pending,
        "panels": panels,
        "offers": {s.value: sum(1 for o in offers if o.status == s) for s in OfferStatus},
    }


def pool_placements(db: Session) -> tuple[list[StudentProfile], dict[int, Placement]]:
    students = db.scalars(select(Student).where(Student.archived.is_(False))).all()
    return [StudentProfile.of(s) for s in students], placements_for_all(db)
