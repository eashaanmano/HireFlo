"""HireFlo Assistant: a chat grounded in the user's own HireFlo data.

How it stays trustworthy
* **Grounding, not memory.** Every request rebuilds a compact context from the database (policy in force, drives,
  and for a student their own eligibility, applications, slots and offers). The model is told to answer only from
  it and to say so when the context doesn't cover a question.
* **The policy engine outranks the model.** For a student question about eligibility or the placement policy, the
  deterministic engine's answer (services/qa.py) is included as an authoritative fact the model must not contradict.
* **Data minimisation.** A student's context contains no name, roll number or e-mail, and never anyone else's
  data. The placement cell's context is aggregates only (counts, percentages, drive totals), never individual
  students, because the model is an outside service.
* **Works without an LLM.** With no provider configured, students get the policy engine's answer and the cell
  gets a short summary, so the feature still demonstrates something useful.
* **Traced end to end.** With LangSmith on (services/tracing.py) each reply is one trace, ``assistant.student`` or
  ``assistant.cell``, that stays open while the answer streams: the grounding context, the policy engine's answer
  and the LLM call are its children and the full answer is its output. The trace holds the chat history and the
  same context the model sees (so no identities either), never the database session or ORM rows.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.timeutil import ensure_utc, utcnow
from ..models import (
    Application,
    AppStatus,
    Drive,
    DriveStatus,
    EligibilityDecision,
    OfferStatus,
    Slot,
    SlotStatus,
    Student,
)
from . import llm, qa, tracing
from .policy import active_policy, placement_for

VISIBLE = (DriveStatus.open, DriveStatus.closed, DriveStatus.live)
MAX_TURNS = 12
MAX_CHARS = 2000

STUDENT_SYSTEM = """You are HireFlo Assistant for a student at NIT Calicut during campus placements.
Answer ONLY from the CONTEXT below, which is the student's own live HireFlo data and the placement policy.
Rules:
- If POLICY ENGINE ANSWER is present, it is authoritative: never contradict its yes/no outcome or its reasons.
- Cite policy codes like (P2) when a rule applies. Use exact company names, CTCs and dates from the context.
- If the context does not contain the answer, say so plainly and suggest where in HireFlo to look.
- Never invent drives, rules, cut-offs, dates or outcomes. Never guess about other students.
- Be brief: at most 6 short sentences or a short bullet list. Times are in IST.
- You cannot take actions (apply, accept, withdraw); tell the student which page to use."""

CELL_SYSTEM = """You are HireFlo Assistant for the NIT Calicut placement cell.
Answer ONLY from the CONTEXT below: aggregate, live figures for the current season (no individual students).
Rules:
- Quote numbers exactly as given; when you compute something (a difference, a share), say how.
- If the context does not cover the question, say so and point to the HireFlo page that does
  (Insights, Drives, Control room, Inbox, Policy, Simulator, Audit log).
- Be brief and practical: at most 6 short sentences or a short bullet list. Times are in IST.
- You cannot change data; suggest the action and where to take it."""


@dataclass
class Reply:
    provider: str
    pieces: Iterator[str]


def _chat_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    history = inputs.get("history") or []
    return {"question": history[-1]["content"] if history else "", "history": history}


def _chat_outputs(items: list[str]) -> dict[str, Any]:
    """``reduce_fn`` for a traced reply: the first item is the provider label, the rest is the answer."""
    return {"provider": items[0] if items else None, "answer": "".join(items[1:])}


def _start(flow: Iterator[str]) -> Reply:
    """Run a reply flow up to its first item (the provider), leaving the answer to stream."""
    provider = next(flow, "summary")
    return Reply(provider, flow)


def _ist(dt: Any) -> str | None:
    if dt is None:
        return None
    t = ensure_utc(dt)
    assert t is not None
    return (t + timedelta(minutes=get_settings().display_utc_offset_min)).strftime("%a %d %b, %H:%M")


def clean_history(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep the last turns, only user/assistant roles, each trimmed; the last message must be the user's."""
    out = [{"role": m["role"], "content": str(m.get("content", ""))[:MAX_CHARS]}
           for m in messages if m.get("role") in ("user", "assistant") and str(m.get("content", "")).strip()]
    out = out[-MAX_TURNS:]
    while out and out[0]["role"] != "user":
        out.pop(0)
    if not out or out[-1]["role"] != "user":
        raise ValueError("The last message must be from the user.")
    return out


# ---------------------------------------------------------------- student context
@tracing.traceable(run_type="tool", name="grounding_context", process_inputs=tracing.only())
def student_context(db: Session, student: Student) -> dict[str, Any]:
    _, cfg = active_policy(db)
    placement = placement_for(db, student.id, cfg)
    now = utcnow()
    decisions = {d.drive_id: d for d in db.scalars(
        select(EligibilityDecision).where(EligibilityDecision.student_id == student.id))}
    apps = {a.drive_id: a for a in student.applications}
    drives = []
    for d in db.scalars(select(Drive).where(Drive.status.in_(VISIBLE)).order_by(Drive.apply_deadline).limit(30)):
        dec = decisions.get(d.id)
        app = apps.get(d.id)
        item: dict[str, Any] = {
            "company": d.company.name, "role": d.role, "ctc_lpa": d.ctc_lpa, "tier": cfg.tier_name(d.ctc_lpa),
            "sector": d.company.sector, "status": d.status.value, "apply_deadline": _ist(d.apply_deadline),
            "eligible": dec.eligible if dec else None,
        }
        if dec and not dec.eligible:
            item["why_not"] = [r.get("message") for r in dec.reasons if not r.get("ok")][:3]
        if app:
            item["my_application"] = {"status": app.status.value, "rounds_cleared": app.rounds_cleared}
        drives.append(item)
    slots = db.scalars(select(Slot).join(Application, Slot.application_id == Application.id)
                       .where(Application.student_id == student.id, Slot.status == SlotStatus.scheduled,
                              Slot.end_at >= now - timedelta(hours=1)).order_by(Slot.start_at).limit(10))
    offers = [a for a in student.applications if a.offer is not None]
    return {
        "now": _ist(now),
        "me": {"programme": student.programme, "branch": student.branch, "cgpa": student.cgpa,
               "backlogs": student.backlogs, "grad_year": student.grad_year, "skills": (student.skills or [])[:15]},
        "placement": {"accepted_offers": [{"company": c, "ctc_lpa": v} for c, v in
                                          zip(placement.companies, placement.accepted_ctcs, strict=False)],
                      "season_over": placement.frozen(cfg),
                      "upgrade_chances_left": max(0, cfg.upgrade_attempts - placement.attempts_used())
                      if placement.best_ctc is not None else None},
        "policy": cfg.describe(),
        "drives": drives,
        "my_interviews": [{"company": s.application.drive.company.name, "round": s.round.name, "panel": s.panel.name,
                           "room": s.panel.room, "start": _ist(s.start_at)} for s in slots],
        "my_offers": [{"company": a.drive.company.name, "ctc_lpa": a.offer.ctc_lpa, "status": a.offer.status.value,
                       "decide_by": _ist(a.offer.deadline) if a.offer.status == OfferStatus.pending else None}
                      for a in offers if a.offer is not None],
    }


def _engine_answer(db: Session, student: Student, question: str) -> qa.Answer | None:
    ans = qa.answer_question(db, student, question)
    if ans.answer.startswith("I can answer questions about"):  # the engine's generic fallback
        return None
    return ans


def student_reply(db: Session, student: Student, messages: list[dict[str, str]]) -> Reply:
    history = clean_history(messages)  # raises ValueError before anything is traced
    return _start(_student_flow(db, student, history))


@tracing.traceable(run_type="chain", name="assistant.student", process_inputs=_chat_inputs,
                   reduce_fn=_chat_outputs, metadata={"audience": "student"})
def _student_flow(db: Session, student: Student, history: list[dict[str, str]]) -> Iterator[str]:
    """Yields the provider label, then the answer's pieces. Everything before the first yield runs inside
    student_reply (while the request's database session is open); the rest streams to the client."""
    question = history[-1]["content"]
    engine = _engine_answer(db, student, question)
    s = get_settings()
    if not (s.llm_enabled and s.assistant_enabled):
        yield "policy-engine"
        yield engine.answer if engine else qa.answer_question(db, student, question).answer
        return
    ctx = student_context(db, student)
    system = STUDENT_SYSTEM + "\n\nCONTEXT (JSON):\n" + json.dumps(ctx, default=str)
    if engine:
        system += ("\n\nPOLICY ENGINE ANSWER for the latest question (authoritative): " + engine.answer
                   + (" Codes: " + ", ".join(c["code"] for c in engine.citations) if engine.citations else ""))
    try:
        provider, pieces = llm.stream([{"role": "system", "content": system}, *history], "chat")
    except llm.LLMError:
        yield "policy-engine"
        yield engine.answer if engine else ("The assistant can't reach its language model right now. "
                                            "Your drives, eligibility and offers are on the Drives and Offers pages.")
        return
    yield provider
    yield from pieces


# ---------------------------------------------------------------- cell context (aggregates only)
@tracing.traceable(run_type="tool", name="grounding_context", process_inputs=tracing.only())
def cell_context(db: Session) -> dict[str, Any]:
    from . import forecast, insights

    _, cfg = active_policy(db)
    stats = insights.placement_stats(db)
    fc = forecast.branch_forecast(db)
    warnings = forecast.early_warning(db)
    inbox = insights.exceptions_inbox(db)
    drives = []
    for d in db.scalars(select(Drive).where(Drive.status.in_(VISIBLE + (DriveStatus.draft,)))
                        .order_by(Drive.apply_deadline).limit(30)):
        apps = [a for a in d.applications if a.status != AppStatus.withdrawn]
        eligible = sum(1 for _ in db.scalars(select(EligibilityDecision.student_id).where(
            EligibilityDecision.drive_id == d.id, EligibilityDecision.eligible.is_(True))))
        drives.append({
            "company": d.company.name, "role": d.role, "ctc_lpa": d.ctc_lpa, "tier": cfg.tier_name(d.ctc_lpa),
            "status": d.status.value, "apply_deadline": _ist(d.apply_deadline), "drive_start": _ist(d.drive_start),
            "eligible": eligible, "applied": len(apps), "target_hires": d.target_hires,
            "selected": sum(a.status == AppStatus.selected for a in apps),
            "rules_pending": sum(1 for r in d.rules if not r.approved),
        })
    return {
        "now": _ist(utcnow()),
        "season": {k: stats[k] for k in ("students", "placed", "placed_pct", "offers", "active_drives")},
        "by_branch": stats["by_branch"],
        "placed_by_tier": stats["by_tier"],
        "forecast": {"totals": fc["totals"], "branches": fc["branches"], "basis": fc["basis"]["method"]},
        "early_warning": {"flagged": len(warnings), "high": sum(w["severity"] == "high" for w in warnings),
                          "top_reasons": _top_reasons(warnings)},
        "inbox": {"items": len(inbox), "by_kind": _count(i["kind"] for i in inbox)},
        "drives": drives,
        "policy": cfg.describe(),
    }


def _count(items: Iterator[str] | Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for k in items:
        out[k] = out.get(k, 0) + 1
    return out


def _top_reasons(warnings: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for w in warnings:
        for r in w["reasons"]:
            key = r.split(" (")[0].split(" times")[0]
            out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1])[:5])


def cell_reply(db: Session, messages: list[dict[str, str]]) -> Reply:
    history = clean_history(messages)  # raises ValueError before anything is traced
    return _start(_cell_flow(db, history))


@tracing.traceable(run_type="chain", name="assistant.cell", process_inputs=_chat_inputs,
                   reduce_fn=_chat_outputs, metadata={"audience": "cell"})
def _cell_flow(db: Session, history: list[dict[str, str]]) -> Iterator[str]:
    """Yields the provider label, then the answer's pieces (see _student_flow)."""
    s = get_settings()
    ctx = cell_context(db)
    if not (s.llm_enabled and s.assistant_enabled):
        sea = ctx["season"]
        yield "summary"
        yield (f"No language model is configured, so here is the season at a glance: {sea['placed']} of "
               f"{sea['students']} students placed ({sea['placed_pct']}%), {sea['offers']} offers, "
               f"{sea['active_drives']} active drives, {ctx['early_warning']['flagged']} early-warning flags and "
               f"{ctx['inbox']['items']} inbox items. Add AZURE_OPENAI_* or GROQ_API_KEY to backend/.env to chat.")
        return
    system = CELL_SYSTEM + "\n\nCONTEXT (JSON):\n" + json.dumps(ctx, default=str)
    try:
        provider, pieces = llm.stream([{"role": "system", "content": system}, *history], "chat")
    except llm.LLMError:
        yield "summary"
        yield "The assistant can't reach its language model right now. The Insights page has the same figures."
        return
    yield provider
    yield from pieces
