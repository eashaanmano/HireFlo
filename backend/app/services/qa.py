"""Policy Q&A grounded in the active policy and the drives' approved rules.

Answers are computed by the same policy engine that decides eligibility, so the assistant can never
contradict the system. When an LLM is configured it only rewrites the computed answer in plainer words;
citations always come from the engine. With LangSmith on, each question is a ``policy_qa`` trace holding the
engine's answer (``policy_engine``) and the LLM rewrite; only the question is recorded as input, never the student.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..models import Drive, DriveStatus, Student
from . import llm, tracing
from .eligibility import StudentProfile, evaluate, rule_specs
from .policy import PolicyConfig, active_policy, placement_for

log = logging.getLogger("driveops.qa")


@dataclass
class Answer:
    answer: str
    citations: list[dict[str, str]] = field(default_factory=list)
    drives: list[int] = field(default_factory=list)
    engine: str = "policy-engine"


def _mentioned_drives(question: str, drives: list[Drive]) -> list[tuple[int, Drive]]:
    """Companies named in the question, as (position, drive), in the order they appear."""
    q = question.lower()
    hits: list[tuple[int, Drive]] = []
    for d in drives:
        name = d.company.name.lower()
        first = name.split()[0].rstrip(".")
        full = re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", q)
        pos = full.start() if full else -1
        if pos < 0 and len(first) >= 3 and first != name:
            m = re.search(rf"\b{re.escape(first)}\b", q)
            pos = m.start() if m else -1
        if pos >= 0:
            hits.append((pos, d))
    hits.sort(key=lambda h: h[0])
    seen: set[int] = set()
    out = []
    for pos, d in hits:
        if d.company_id not in seen:
            seen.add(d.company_id)
            out.append((pos, d))
    return out


def _cite(cfg: PolicyConfig, *codes: str) -> list[dict[str, str]]:
    rules = {r["code"]: r for r in cfg.describe()}
    return [rules[c] for c in codes if c in rules]


@tracing.traceable(run_type="tool", name="policy_engine", process_inputs=tracing.only("question"))
def answer_question(db: Session, student: Student, question: str) -> Answer:
    _, cfg = active_policy(db)
    placement = placement_for(db, student.id, cfg)
    profile = StudentProfile.of(student)
    drives = list(db.scalars(select(Drive).where(Drive.status != DriveStatus.cancelled)).all())
    hits = _mentioned_drives(question, drives)
    mentioned = [d for _, d in hits]
    q = question.lower()
    verb = re.search(r"\b(accept|accepted|hold|holding|take|took|get|got|have)\b", q)
    hypothetical = verb is not None and bool(re.search(r"\b(if|after|once|while)\b", q))

    # 1) "Can I sit for Y if I accept X?" / "If I accept X, can I sit for Y?"
    if hypothetical and verb is not None and len(mentioned) >= 2:
        after_verb = [d for pos, d in hits if pos > verb.start()]
        offer_drive = after_verb[0] if after_verb else mentioned[0]
        target = next(d for d in mentioned if d.id != offer_drive.id)
        hyp = placement.with_offer(offer_drive.ctc_lpa, offer_drive.company.name, offer_drive.company.sector)
        dec = evaluate(profile, rule_specs(target.rules), cfg, hyp, target.ctc_lpa, target.id)
        failed = [r for r in dec.reasons if not r.ok]
        codes = sorted({r.code for r in dec.reasons if r.code.startswith("P")} | {"P0"})
        if dec.eligible:
            text = (f"Yes. With a {cfg.tier_name(offer_drive.ctc_lpa)} offer from {offer_drive.company.name} "
                    f"({offer_drive.ctc_lpa:g} LPA), you could still sit for {target.company.name}, a "
                    f"{cfg.tier_name(target.ctc_lpa)} drive at {target.ctc_lpa:g} LPA.")
        else:
            text = (f"No. If you accept {offer_drive.company.name}, you couldn't sit for {target.company.name}: "
                    + "; ".join(r.message for r in failed) + ".")
        return Answer(text, _cite(cfg, *codes), [offer_drive.id, target.id])

    # 2) "Am I eligible for Y?" / "Why can't I apply to Y?"
    if mentioned:
        d = mentioned[0]
        dec = evaluate(profile, rule_specs(d.rules), cfg, placement, d.ctc_lpa, d.id)
        failed = [r for r in dec.reasons if not r.ok]
        if dec.eligible:
            text = f"Yes, you're eligible for {d.company.name} ({d.role}). " + \
                   " ".join(f"{r.message}." for r in dec.reasons if r.ok)[:600]
        else:
            text = f"You're not eligible for {d.company.name} ({d.role}) because " + \
                   "; ".join(r.message.lower() if i else r.message for i, r in enumerate(failed)) + "."
            if any(r.fixable for r in failed):
                text += " Clearing your backlogs would change this."
            text += " If you think this is wrong, raise a dispute from the drive page."
        codes = sorted({r.code for r in dec.reasons if r.code.startswith("P")})
        return Answer(text, _cite(cfg, *codes), [d.id])

    # 3) Policy topics
    if re.search(r"how many offers|offer limit|more than one offer|second offer|max(imum)? offers", q):
        return Answer(f"You can accept at most {cfg.max_offers} offer(s) in total. You've accepted "
                      f"{len(placement.accepted_ctcs)} so far.", _cite(cfg, "P2"))
    if re.search(r"chance|attempt|psu|one offer|after (an|my) offer", q):
        thr = f"{cfg.freeze_at_ctc_lpa:g} LPA"
        text = (f"If you accept an offer below {thr}, you can still sit for {cfg.upgrade_attempts} more drive(s) "
                f"paying {thr} or more. An offer of {thr} or more, or any PSU offer, ends your season.")
        if placement.best_ctc is not None:
            if placement.frozen(cfg):
                text += " Your accepted offer has already ended your season."
            else:
                left = max(0, cfg.upgrade_attempts - placement.attempts_used())
                text += f" You have {left} chance(s) left."
        return Answer(text, _cite(cfg, "P3", "P6"))
    if re.search(r"dream|tier|super", q):
        tiers = ", ".join(f"{t.name} from {t.min_ctc_lpa:g} LPA" for t in cfg.tiers)
        extra = " After you accept an offer you can only sit for higher tiers." if cfg.upgrade_only else ""
        return Answer(f"Drives are tiered by CTC: {tiers}.{extra}", _cite(cfg, "P0", "P1", "P3", "P6"))
    if re.search(r"deadline|how long.*(accept|decide)|respond", q):
        return Answer(f"You have {cfg.offer_deadline_hours} hours to accept or decline an offer.", _cite(cfg, "P5"))
    if "backlog" in q:
        strict = [f"{d.company.name} allows {int(r.value)}" for d in drives for r in d.rules
                  if r.field == "backlogs" and r.approved and d.status in (DriveStatus.open, DriveStatus.draft)]
        detail = "; ".join(strict[:6]) if strict else "No upcoming drive restricts backlogs."
        return Answer(f"You have {student.backlogs} active backlog(s). Upcoming drive limits: {detail}.")

    return Answer("I can answer questions about eligibility, tiers, offer limits and deadlines, for example "
                  "\"Can I sit for <company> if I accept <company>?\". Here is the policy in force.",
                  cfg.describe(), engine="policy-engine")


@tracing.traceable(run_type="chain", name="policy_qa", process_inputs=tracing.only("question"))
def answer(db: Session, student: Student, question: str) -> Answer:
    base = answer_question(db, student, question)
    settings = get_settings()
    if not settings.llm_enabled:
        return base
    prompt = ("Rewrite this answer for a student in two short, plain sentences. Do not add facts, do not change "
              f"the yes/no outcome, and do not invent rules.\n\nQuestion: {question}\nAnswer: {base.answer}")
    try:
        out = llm.complete([{"role": "user", "content": prompt}], "rewrite", max_tokens=200, settings=settings)
        if out.text:
            return Answer(out.text, base.citations, base.drives, engine=f"policy-engine+{out.provider}")
    except (llm.LLMError, httpx.HTTPError, KeyError, ValueError) as exc:
        log.warning("LLM rewrite failed: %s", exc)
    return base
