"""Recruiter API behind a login-free, drive-scoped, expiring magic link.

Consent gating: recruiters see aggregate counts for the whole pool, but personal details only for students who
applied to their drive. They never see other companies' offers or selections.
"""
from __future__ import annotations

import csv
import io
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, PlainTextResponse, Response
from sqlalchemy import select

from ..core.config import get_settings
from ..core.deps import DbSession, RecruiterCtx
from ..core.ratelimit import rate_limit
from ..core.security import create_link_token
from ..core.timeutil import utcnow
from ..models import (
    Application,
    AppStatus,
    EligibilityRule,
    Offer,
    OfferStatus,
    Panel,
    Resume,
    Round,
    RoundKind,
    Slot,
    SlotStatus,
    Student,
)
from ..schemas import (
    ApplicationOut,
    CriteriaChangeIn,
    OfferIn,
    OfferOut,
    PanelIn,
    PanelOut,
    PoolPreviewIn,
    ResultsCsv,
    StageUpdate,
)
from ..services import analytics, insights, live, notifications
from ..services.eligibility import RuleSpec, evaluate, preview_pool, rule_specs
from ..services.events import emit
from ..services.exports import applications_csv, resume_bundle
from ..services.policy import Placement, active_policy
from .cell_drives import apply_stage_update
from .common import application_out, bad_request, get_or_404, offer_out, resume_response, slot_out

router = APIRouter(prefix="/api/r", tags=["recruiter link"],
                   dependencies=[Depends(rate_limit("recruiter", 240, 60))])


def _visible_apps(ctx: RecruiterCtx) -> list[Application]:
    return [a for a in ctx.drive.applications if a.status != AppStatus.withdrawn]


@router.get("/overview")
def overview(ctx: RecruiterCtx, db: DbSession) -> dict[str, Any]:
    data = insights.recruiter_overview(db, ctx.drive)
    _, cfg = active_policy(db)
    data["drive"]["tier"] = cfg.tier_name(ctx.drive.ctc_lpa)
    data["drive"]["jd_text"] = ctx.drive.jd_text
    data["drive"]["skills"] = ctx.drive.skills
    data["rules"] = [{"field": r.field, "op": r.op, "value": r.value, "approved": r.approved,
                      "source_text": r.source_text} for r in ctx.drive.rules]
    data["rounds"] = [{"id": r.id, "name": r.name, "kind": r.kind.value, "seq": r.seq,
                       "duration_min": r.duration_min,
                       "panels": [{"id": p.id, "name": p.name, "room": p.room, "interviewer": p.interviewer}
                                  for p in r.panels]} for r in ctx.drive.rounds]
    return data


@router.get("/analytics")
def drive_analytics(ctx: RecruiterCtx, db: DbSession) -> dict[str, Any]:
    return analytics.recruiter_analytics(db, ctx.drive)


@router.get("/schedule")
def drive_schedule(ctx: RecruiterCtx, db: DbSession) -> list[dict[str, Any]]:
    """Interview slots for this drive (applicants only), for the timeline view."""
    proj = live.drive_projection(db, ctx.drive)
    by_slot = {sp.slot.id: sp for items in proj.values() for sp in items}
    rows = db.scalars(select(Slot).where(Slot.drive_id == ctx.drive.id, Slot.status != SlotStatus.cancelled)
                      .order_by(Slot.start_at)).all()
    return [slot_out(s, by_slot.get(s.id)).model_dump(mode="json") for s in rows]


@router.post("/pool-preview")
def pool_preview(body: PoolPreviewIn, ctx: RecruiterCtx, db: DbSession) -> dict[str, Any]:
    """What-if: how many students would be eligible under these criteria? Aggregates only."""
    profiles, placements = insights.pool_placements(db)
    _, cfg = active_policy(db)
    current = rule_specs(ctx.drive.rules)
    base = {r.field: r for r in current}
    if body.cgpa_min is not None:
        base["cgpa"] = RuleSpec("cgpa", ">=", body.cgpa_min)
    if body.max_backlogs is not None:
        base["backlogs"] = RuleSpec("backlogs", "<=", body.max_backlogs)
    if body.branches is not None:
        if body.branches:
            base["branch"] = RuleSpec("branch", "in", [b.upper() for b in body.branches])
        else:
            base.pop("branch", None)
    if body.programmes is not None:
        if body.programmes:
            base["programme"] = RuleSpec("programme", "in", [p.upper() for p in body.programmes])
        else:
            base.pop("programme", None)
    what_if = list(base.values())
    now_count = preview_pool(profiles, current, cfg, placements, ctx.drive.ctc_lpa, ctx.drive.id)
    new_count = preview_pool(profiles, what_if, cfg, placements, ctx.drive.ctc_lpa, ctx.drive.id)
    # Skill-match distribution for the hypothetical pool (counts per bucket, no identities).
    skills = [s.lower() for s in ctx.drive.skills or []]
    buckets = {"0-25%": 0, "25-50%": 0, "50-75%": 0, "75-100%": 0}
    if skills:
        students = {s.id: s for s in db.scalars(select(Student).where(Student.archived.is_(False)))}
        for p in profiles:
            if not evaluate(p, what_if, cfg, placements.get(p.id, Placement()), ctx.drive.ctc_lpa,
                            ctx.drive.id).eligible:
                continue
            have = {x.lower() for x in students[p.id].skills}
            frac = sum(1 for s in skills if s in have) / len(skills)
            key = "75-100%" if frac >= 0.75 else "50-75%" if frac >= 0.5 else "25-50%" if frac >= 0.25 else "0-25%"
            buckets[key] += 1
    return {"current": now_count, "what_if": new_count, "skill_match": buckets, "total_students": len(profiles)}


@router.post("/criteria-request", status_code=status.HTTP_201_CREATED)
def criteria_request(body: CriteriaChangeIn, ctx: RecruiterCtx, db: DbSession) -> dict[str, Any]:
    """Recruiter proposes changed criteria; they arrive as unapproved rules in the cell's exceptions inbox."""
    note = f"Requested by {ctx.drive.company.name}" + (f": {body.note}" if body.note else "")
    proposals: list[tuple[str, str, Any]] = []
    if body.cgpa_min is not None:
        proposals.append(("cgpa", ">=", body.cgpa_min))
    if body.max_backlogs is not None:
        proposals.append(("backlogs", "<=", body.max_backlogs))
    if body.branches:
        proposals.append(("branch", "in", [b.upper() for b in body.branches]))
    if body.programmes:
        proposals.append(("programme", "in", [p.upper() for p in body.programmes]))
    if not proposals:
        raise bad_request("Change at least one criterion.")
    for field, op, value in proposals:
        existing = next((r for r in ctx.drive.rules if r.field == field), None)
        if existing is not None:
            existing.op, existing.value, existing.approved, existing.source_text = op, value, False, note
            existing.confidence = 1.0
        else:
            ctx.drive.rules.append(EligibilityRule(field=field, op=op, value=value, source_text=note,
                                                   confidence=1.0, approved=False))
    emit(db, "criteria.requested", ctx.actor, entity="drive", entity_id=ctx.drive.id, drive_id=ctx.drive.id,
         payload={"fields": [p[0] for p in proposals]})
    db.commit()
    return {"requested": len(proposals)}


@router.get("/applicants", response_model=list[ApplicationOut])
def applicants(ctx: RecruiterCtx) -> list[ApplicationOut]:
    return [application_out(a) for a in sorted(_visible_apps(ctx), key=lambda a: a.student.roll_no)]


@router.get("/applicants.csv")
def applicants_csv(ctx: RecruiterCtx) -> PlainTextResponse:
    name = ctx.drive.company.name.replace(" ", "_")
    return PlainTextResponse(applications_csv(_visible_apps(ctx)), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}_candidates.csv"'})


@router.post("/applicants/stage")
def stage(body: StageUpdate, ctx: RecruiterCtx, db: DbSession) -> dict[str, int]:
    if body.round_id is not None:
        rnd = get_or_404(db, Round, body.round_id, "Round")
        if rnd.kind == RoundKind.interview and body.action in ("pass_round", "fail_round"):
            raise bad_request("Interview results are recorded by panels on the day.")
    changed = apply_stage_update(db, ctx.drive, body, ctx.actor)
    db.commit()
    return {"changed": changed}


@router.post("/results")
def upload_results(body: ResultsCsv, ctx: RecruiterCtx, db: DbSession) -> dict[str, Any]:
    """CSV with columns roll_no,result (pass/fail) — the format most ATS exports can produce."""
    rnd = get_or_404(db, Round, body.round_id, "Round")
    if rnd.drive_id != ctx.drive.id:
        raise bad_request("That round belongs to another drive.")
    by_roll = {a.student.roll_no.upper(): a for a in _visible_apps(ctx)}
    reader = csv.DictReader(io.StringIO(body.csv_text.lstrip("﻿")))
    fields = {f.strip().lower(): f for f in (reader.fieldnames or [])}
    roll_col = fields.get("roll_no") or fields.get("candidate_id") or fields.get("roll no")
    res_col = fields.get("result") or fields.get("status")
    if not roll_col or not res_col:
        raise bad_request("The CSV needs 'roll_no' (or 'candidate_id') and 'result' columns.")
    passes, fails, unknown = [], [], []
    for row in reader:
        roll = (row.get(roll_col) or "").strip().upper()
        result = (row.get(res_col) or "").strip().lower()
        app = by_roll.get(roll)
        if app is None:
            unknown.append(roll)
        elif result in ("pass", "passed", "selected", "yes", "cleared"):
            passes.append(app.id)
        elif result in ("fail", "failed", "rejected", "no"):
            fails.append(app.id)
        else:
            unknown.append(roll)
    changed = 0
    if passes:
        changed += apply_stage_update(db, ctx.drive, StageUpdate(application_ids=passes, action="pass_round",
                                                                 round_id=rnd.id), ctx.actor)
    if fails:
        changed += apply_stage_update(db, ctx.drive, StageUpdate(application_ids=fails, action="fail_round",
                                                                 round_id=rnd.id), ctx.actor)
    emit(db, "results.uploaded", ctx.actor, entity="round", entity_id=rnd.id, drive_id=ctx.drive.id,
         payload={"pass": len(passes), "fail": len(fails), "unknown": len(unknown)})
    db.commit()
    return {"changed": changed, "passed": len(passes), "failed": len(fails), "unmatched": unknown[:50]}


@router.get("/resumes.zip")
def resumes_zip(ctx: RecruiterCtx) -> Response:
    shortlisted = [a for a in _visible_apps(ctx) if a.status in (AppStatus.in_process, AppStatus.selected)]
    shortlisted.sort(key=lambda a: (-a.rounds_cleared, -a.student.cgpa))
    data = resume_bundle(shortlisted, Path(get_settings().upload_dir))
    name = ctx.drive.company.name.replace(" ", "_")
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}_resumes.zip"'})


@router.get("/resumes/{application_id}")
def resume(application_id: int, ctx: RecruiterCtx, db: DbSession) -> FileResponse:
    app = get_or_404(db, Application, application_id, "Application")
    if app.drive_id != ctx.drive.id or app.status == AppStatus.withdrawn:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resume not found.")
    r = db.get(Resume, app.resume_id) if app.resume_id else (app.student.resumes[0] if app.student.resumes else None)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This candidate has no resume on file.")
    return resume_response(r, f"{app.student.roll_no}.pdf")


@router.post("/panels", response_model=PanelOut, status_code=status.HTTP_201_CREATED)
def add_panel(body: PanelIn, ctx: RecruiterCtx, db: DbSession) -> Any:
    rnd = get_or_404(db, Round, body.round_id, "Round")
    if rnd.drive_id != ctx.drive.id or rnd.kind != RoundKind.interview:
        raise bad_request("Panels can only be added to this drive's interview rounds.")
    p = Panel(drive_id=ctx.drive.id, **body.model_dump())
    db.add(p)
    db.flush()
    emit(db, "panel.added", ctx.actor, entity="panel", entity_id=p.id, drive_id=ctx.drive.id)
    db.commit()
    return p


@router.get("/panel-links")
def panel_links(ctx: RecruiterCtx) -> list[dict[str, Any]]:
    base = get_settings().public_url
    d = ctx.drive
    return [{"panel_id": p.id, "name": p.name, "round": r.name, "room": p.room,
             "url": f"{base}/p/{create_link_token('panel', d.id, d.link_version, p.id)}"}
            for r in d.rounds for p in r.panels]


@router.get("/offers", response_model=list[OfferOut])
def offers(ctx: RecruiterCtx, db: DbSession) -> list[OfferOut]:
    _, cfg = active_policy(db)
    return [offer_out(a, cfg) for a in _visible_apps(ctx) if a.offer is not None]


@router.post("/offers", response_model=OfferOut, status_code=status.HTTP_201_CREATED)
def release_offer(body: OfferIn, ctx: RecruiterCtx, db: DbSession) -> OfferOut:
    app = get_or_404(db, Application, body.application_id, "Application")
    if app.drive_id != ctx.drive.id:
        raise bad_request("That candidate isn't part of this drive.")
    if app.status != AppStatus.selected:
        raise bad_request("Only candidates who cleared every round can receive an offer.")
    if app.offer is not None:
        raise bad_request("This candidate already has an offer.")
    _, cfg = active_policy(db)
    deadline = body.deadline or utcnow() + timedelta(hours=cfg.offer_deadline_hours)
    offer = Offer(application_id=app.id, ctc_lpa=body.ctc_lpa, deadline=deadline, status=OfferStatus.pending)
    db.add(offer)
    db.flush()
    db.refresh(app)
    emit(db, "offer.released", ctx.actor, entity="offer", entity_id=offer.id, drive_id=ctx.drive.id,
         student_id=app.student_id)
    notifications.notify(db, [app.student_id], f"Offer from {ctx.drive.company.name}",
                         f"{body.ctc_lpa:g} LPA. Open Offers to see what accepting changes before you decide.",
                         link="/student/offers")
    db.commit()
    return offer_out(app, cfg)
