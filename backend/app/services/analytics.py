"""Chart-ready analytics and calendar feeds for the three actors. All series are derived from operational data."""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.timeutil import ensure_utc, utcnow
from ..models import (
    Application,
    AppStatus,
    Drive,
    DriveStatus,
    EligibilityDecision,
    Offer,
    OfferStatus,
    RoundKind,
    Slot,
    SlotStatus,
    Student,
)
from .insights import application_funnel
from .policy import active_policy, placements_for_all

SEASON = (DriveStatus.open, DriveStatus.closed, DriveStatus.live, DriveStatus.completed, DriveStatus.draft)
CGPA_BUCKETS = [(6.0, 6.5), (6.5, 7.0), (7.0, 7.5), (7.5, 8.0), (8.0, 8.5), (8.5, 9.0), (9.0, 9.5), (9.5, 10.01)]


def _iso(dt: datetime | None) -> str | None:
    d = ensure_utc(dt)
    return d.isoformat() if d else None


def _rate(num: int, den: int) -> float | None:
    return round(100 * num / den, 1) if den else None


def _week_key(dt: datetime) -> str:
    monday = (dt - timedelta(days=dt.weekday())).date()
    return monday.isoformat()


# ---------------------------------------------------------------- student
def student_calendar(db: Session, student: Student) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for s in db.scalars(select(Slot).join(Application, Slot.application_id == Application.id)
                        .where(Application.student_id == student.id, Slot.status != SlotStatus.cancelled)):
        events.append({"kind": "interview", "start": _iso(s.start_at), "end": _iso(s.end_at),
                       "title": f"{s.application.drive.company.name}: {s.round.name}",
                       "detail": f"{s.panel.name}, {s.panel.room}", "status": s.status.value,
                       "link": "/student/schedule"})
    applied = {a.drive_id for a in student.applications}
    eligible = {d.drive_id for d in db.scalars(select(EligibilityDecision).where(
        EligibilityDecision.student_id == student.id, EligibilityDecision.eligible.is_(True)))}
    for d in db.scalars(select(Drive).where(Drive.status == DriveStatus.open)):
        if d.apply_deadline is None:
            continue
        mine = d.id in applied
        if d.id in eligible or mine:
            events.append({"kind": "deadline", "start": _iso(d.apply_deadline), "end": None,
                           "title": f"{d.company.name} applications close",
                           "detail": f"{d.role}{' (applied)' if mine else ''}",
                           "status": "applied" if mine else "open", "link": f"/student/drives/{d.id}"})
    for a in student.applications:
        d = a.drive
        if d.drive_start and a.status in (AppStatus.in_process, AppStatus.selected):
            events.append({"kind": "drive", "start": _iso(d.drive_start), "end": None,
                           "title": f"{d.company.name}: drive day", "detail": d.location or d.role,
                           "status": d.status.value, "link": f"/student/drives/{d.id}"})
    for a in student.applications:
        if a.offer is not None:
            events.append({"kind": "offer", "start": _iso(a.offer.deadline), "end": None,
                           "title": f"{a.drive.company.name} offer deadline", "detail": f"{a.offer.ctc_lpa:g} LPA",
                           "status": a.offer.status.value, "link": "/student/offers"})
    events.sort(key=lambda e: e["start"] or "")
    return events


def _pass_rates(apps: list[Application]) -> tuple[float | None, float | None]:
    f = application_funnel(apps)
    return _rate(f.test_cleared, f.test_reached), _rate(f.interview_cleared, f.interview_reached)


def student_analytics(db: Session, student: Student, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    _, cfg = active_policy(db)
    drives = list(db.scalars(select(Drive).where(Drive.status.in_(SEASON))))
    decisions = {d.drive_id: d.eligible for d in db.scalars(
        select(EligibilityDecision).where(EligibilityDecision.student_id == student.id))}
    by_tier: dict[str, dict[str, int]] = {t.name: {"eligible": 0, "total": 0} for t in cfg.tiers}
    for d in drives:
        t = cfg.tier_name(d.ctc_lpa)
        by_tier[t]["total"] += 1
        by_tier[t]["eligible"] += bool(decisions.get(d.id))
    mine = [a for a in student.applications if a.status != AppStatus.withdrawn]
    batch = list(db.scalars(select(Application).where(Application.status != AppStatus.withdrawn)))
    my_test, my_int = _pass_rates(mine)
    b_test, b_int = _pass_rates(batch)
    weeks = [_week_key(now - timedelta(weeks=i)) for i in range(7, -1, -1)]
    per_week = Counter(_week_key(ensure_utc(a.created_at)) for a in mine if a.created_at)  # type: ignore[arg-type]
    ctcs = sorted(d.ctc_lpa for d in drives if decisions.get(d.id))
    cutoffs = []
    for d in sorted((d for d in drives if d.status in (DriveStatus.open, DriveStatus.draft, DriveStatus.closed,
                                                        DriveStatus.live)), key=lambda d: -d.ctc_lpa):
        rule = next((r for r in d.rules if r.field == "cgpa" and r.approved), None)
        sector = (d.company.sector or "")
        cutoffs.append({"drive_id": d.id, "company": d.company.name, "role": d.role, "ctc_lpa": d.ctc_lpa,
                        "tier": "PSU" if sector.upper() == "PSU" else cfg.tier_name(d.ctc_lpa),
                        "cutoff": float(rule.value) if rule else None, "eligible": decisions.get(d.id)})
    return {
        "cgpa": student.cgpa,
        "cutoffs": cutoffs,
        "weekly_applications": [{"week": w, "count": per_week.get(w, 0)} for w in weeks],
        "by_tier": [{"tier": k, **v} for k, v in by_tier.items()],
        "pass_rates": [
            {"stage": "Online tests", "you": my_test, "batch": b_test},
            {"stage": "Interviews", "you": my_int, "batch": b_int},
        ],
        "eligible_ctc": {"count": len(ctcs), "median": statistics.median(ctcs) if ctcs else None,
                         "max": ctcs[-1] if ctcs else None},
    }


# ---------------------------------------------------------------- recruiter
def recruiter_analytics(db: Session, drive: Drive) -> dict[str, Any]:
    apps = [a for a in drive.applications if a.status != AppStatus.withdrawn]
    branches: dict[str, dict[str, int]] = defaultdict(lambda: {"applicants": 0, "shortlisted": 0, "selected": 0})
    hist = [0] * len(CGPA_BUCKETS)
    daily: Counter[str] = Counter()
    skills = [s.lower() for s in drive.skills or []]
    skill_have: Counter[str] = Counter()
    matches: list[float] = []
    for a in apps:
        st = a.student
        b = branches[st.branch]
        b["applicants"] += 1
        b["shortlisted"] += a.status in (AppStatus.in_process, AppStatus.selected) or a.rejected_round is not None
        b["selected"] += a.status == AppStatus.selected
        for i, (lo, hi) in enumerate(CGPA_BUCKETS):
            if lo <= st.cgpa < hi:
                hist[i] += 1
                break
        if a.created_at:
            daily[ensure_utc(a.created_at).date().isoformat()] += 1  # type: ignore[union-attr]
        have = {x.lower() for x in st.skills}
        for s in skills:
            skill_have[s] += s in have
        if skills:
            matches.append(sum(1 for s in skills if s in have) / len(skills))
    panels = []
    for rnd in drive.rounds:
        if rnd.kind != RoundKind.interview:
            continue
        for p in rnd.panels:
            done = [s for s in db.scalars(select(Slot).where(Slot.panel_id == p.id, Slot.status == SlotStatus.done))]
            durs = [(ensure_utc(s.actual_end) - ensure_utc(s.actual_start)).total_seconds() / 60  # type: ignore[operator]
                    for s in done if s.actual_start and s.actual_end]
            panels.append({"panel": f"{p.name} ({rnd.name})", "pass": sum(s.result == "pass" for s in done),
                           "fail": sum(s.result == "fail" for s in done),
                           "avg_min": round(statistics.fmean(durs), 1) if durs else None,
                           "planned_min": rnd.duration_min})
    f = application_funnel(apps)
    offers = [a.offer for a in apps if a.offer is not None]
    return {
        "totals": {
            "applicants": len(apps),
            "shortlisted": f.shortlisted,
            "selected": sum(a.status == AppStatus.selected for a in apps),
            "offers_accepted": sum(o.status == OfferStatus.accepted for o in offers),
            "avg_cgpa": round(statistics.fmean(a.student.cgpa for a in apps), 2) if apps else None,
            "median_skill_match": round(100 * statistics.median(matches)) if matches else None,
        },
        "branches": [{"branch": k, **v} for k, v in sorted(branches.items(), key=lambda kv: -kv[1]["applicants"])],
        "cgpa_histogram": [{"bucket": f"{lo:g}–{min(hi, 10):g}", "count": n} for (lo, hi), n in zip(
            CGPA_BUCKETS, hist, strict=True)],
        "skill_coverage": [{"skill": s, "pct": round(100 * skill_have[s] / len(apps)) if apps else 0}
                           for s in skills],
        "daily": [{"date": d, "applications": n} for d, n in sorted(daily.items())],
        "panels": panels,
        "funnel": [
            {"stage": "Applied", "count": f.applied},
            {"stage": "Shortlisted", "count": f.shortlisted},
            {"stage": "Test cleared", "count": f.test_cleared},
            {"stage": "Interview cleared", "count": f.interview_cleared},
            {"stage": "Offer", "count": f.offers},
        ],
    }


# ---------------------------------------------------------------- placement cell
def season_analytics(db: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    _, cfg = active_policy(db)
    offers = list(db.scalars(select(Offer)))
    accepted = [o for o in offers if o.status == OfferStatus.accepted]
    weeks: Counter[str] = Counter()
    for o in accepted:
        when = ensure_utc(o.decided_at or o.created_at)
        if when:
            weeks[_week_key(when)] += 1
    week_keys = [_week_key(now - timedelta(weeks=i)) for i in range(7, -1, -1)]
    running = 0
    offers_by_week = []
    for w in week_keys:
        running += weeks.get(w, 0)
        offers_by_week.append({"week": w, "accepted": weeks.get(w, 0), "cumulative": running})
    buckets = [(0, 5), (5, 10), (10, 15), (15, 20), (20, 30), (30, 100)]
    ctc_hist = [{"band": f"{lo}–{hi} LPA" if hi < 100 else f"{lo}+ LPA",
                 "offers": sum(lo <= o.ctc_lpa < hi for o in accepted)} for lo, hi in buckets]
    by_company: Counter[str] = Counter()
    for o in offers:
        if o.status in (OfferStatus.accepted, OfferStatus.pending):
            by_company[o.application.drive.company.name] += 1
    placements = placements_for_all(db, cfg)
    by_tier = Counter(cfg.tier_name(p.best_ctc) for p in placements.values() if p.best_ctc is not None)
    psu = sum(1 for p in placements.values() if any(s.upper() == "PSU" for s in p.sectors))
    students = db.scalars(select(Student).where(Student.archived.is_(False))).all()
    season_over = sum(1 for p in placements.values() if p.frozen(cfg))
    eligible_counts = Counter(did for did, in db.execute(select(EligibilityDecision.drive_id).where(
        EligibilityDecision.eligible.is_(True))))
    drives = []
    for d in db.scalars(select(Drive).where(Drive.status.in_(SEASON)).order_by(Drive.ctc_lpa.desc())):
        apps = [a for a in d.applications if a.status != AppStatus.withdrawn]
        drives.append({"id": d.id, "company": d.company.name, "role": d.role, "ctc_lpa": d.ctc_lpa,
                       "tier": cfg.tier_name(d.ctc_lpa), "sector": d.company.sector, "status": d.status.value,
                       "deadline": _iso(d.apply_deadline), "drive_day": _iso(d.drive_start),
                       "eligible": eligible_counts.get(d.id, 0), "applied": len(apps),
                       "selected": sum(a.status == AppStatus.selected for a in apps)})
    ctcs = [o.ctc_lpa for o in accepted]
    return {
        "kpis": {"offers_accepted": len(accepted), "offers_pending": sum(o.status == OfferStatus.pending
                                                                          for o in offers),
                 "median_ctc": statistics.median(ctcs) if ctcs else None, "max_ctc": max(ctcs) if ctcs else None,
                 "season_over": season_over, "still_in_play": len(students) - season_over, "psu_offers": psu},
        "offers_by_week": offers_by_week,
        "ctc_hist": ctc_hist,
        "by_tier": [{"tier": t.name, "students": by_tier.get(t.name, 0)} for t in cfg.tiers],
        "top_recruiters": [{"company": c, "offers": n} for c, n in by_company.most_common(8)],
        "drives": drives,
    }


def season_calendar(db: Session) -> list[dict[str, Any]]:
    """Every drive's application deadline and drive day, for the placement cell's season calendar."""
    events: list[dict[str, Any]] = []
    for d in db.scalars(select(Drive).where(Drive.status.in_(SEASON))):
        link = f"/cell/drives/{d.id}"
        if d.apply_deadline:
            events.append({"kind": "deadline", "start": _iso(d.apply_deadline), "end": None,
                           "title": f"{d.company.name} applications close", "detail": f"{d.role}, {d.ctc_lpa:g} LPA",
                           "status": d.status.value, "link": link})
        if d.drive_start:
            events.append({"kind": "drive", "start": _iso(d.drive_start), "end": None,
                           "title": f"{d.company.name}: drive day", "detail": d.role, "status": d.status.value,
                           "link": link if d.status != DriveStatus.live else f"{link}/control"})
        for a in d.applications:
            if a.offer and a.offer.status == OfferStatus.pending:
                events.append({"kind": "offer", "start": _iso(a.offer.deadline), "end": None,
                               "title": f"{d.company.name} offer deadline", "detail": a.student.name,
                               "status": "pending", "link": link})
    events.sort(key=lambda e: e["start"] or "")
    return events
