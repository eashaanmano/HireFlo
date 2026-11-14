"""Offer decision simulator: what accepting (or declining) a pending offer does to the rest of a student's season.

Eligibility after accepting comes from the same policy engine that decides it for real, so the simulator can
never disagree with the system. Odds are a deliberately simple, explainable estimate:

    odds(drive) = tier base rate (this season's completed drives) x personal factor (your pass rate vs the batch)

The personal factor is shrunk towards 1 with a prior of a few attempts so one lucky test doesn't dominate, and is
clamped, so the numbers stay modest. The UI labels them as estimates.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Application, AppStatus, Drive, DriveStatus, Offer, Student
from .eligibility import StudentProfile, evaluate, rule_specs
from .insights import application_funnel
from .policy import PolicyConfig, active_policy, placement_for

UPCOMING = (DriveStatus.open, DriveStatus.draft)
DEFAULT_BASE_RATE = 0.12
PRIOR_ATTEMPTS = 4.0
FACTOR_RANGE = (0.5, 1.8)
ODDS_RANGE = (0.02, 0.85)


@dataclass(frozen=True)
class OddsModel:
    tier_rates: dict[str, float]
    personal_factor: float

    def odds(self, tier: str) -> float:
        base = self.tier_rates.get(tier, DEFAULT_BASE_RATE)
        return round(min(ODDS_RANGE[1], max(ODDS_RANGE[0], base * self.personal_factor)), 3)


def p_at_least_one(odds: list[float]) -> float:
    """Chance of at least one offer from independent attempts."""
    miss = 1.0
    for p in odds:
        miss *= 1.0 - p
    return round(1.0 - miss, 3)


def personal_factor(mine: list[Application], batch: list[Application]) -> float:
    """Your round clearance rate relative to the batch, shrunk towards the batch with a small prior."""
    def cleared_attempted(apps: list[Application]) -> tuple[int, int]:
        f = application_funnel(apps)
        return f.test_cleared + f.interview_cleared, f.test_reached + f.interview_reached

    b_num, b_den = cleared_attempted(batch)
    if b_den == 0:
        return 1.0
    batch_rate = b_num / b_den
    if batch_rate <= 0:
        return 1.0
    y_num, y_den = cleared_attempted(mine)
    shrunk = (y_num + PRIOR_ATTEMPTS * batch_rate) / (y_den + PRIOR_ATTEMPTS)
    return round(min(FACTOR_RANGE[1], max(FACTOR_RANGE[0], shrunk / batch_rate)), 2)


def tier_base_rates(db: Session, cfg: PolicyConfig) -> dict[str, float]:
    """Share of applicants who got selected, per tier, on this season's completed drives."""
    applied: dict[str, int] = defaultdict(int)
    selected: dict[str, int] = defaultdict(int)
    rows = db.scalars(select(Application).join(Drive).where(Drive.status == DriveStatus.completed,
                                                            Application.status != AppStatus.withdrawn)).all()
    for a in rows:
        t = cfg.tier_name(a.drive.ctc_lpa)
        applied[t] += 1
        selected[t] += a.status == AppStatus.selected
    return {t: round(selected[t] / applied[t], 3) for t in applied if applied[t] >= 5}


def build_model(db: Session, student: Student, cfg: PolicyConfig) -> OddsModel:
    batch = list(db.scalars(select(Application).where(Application.status != AppStatus.withdrawn)).all())
    mine = [a for a in batch if a.student_id == student.id]
    return OddsModel(tier_base_rates(db, cfg), personal_factor(mine, batch))


def offer_whatif(db: Session, student: Student, offer: Offer) -> dict[str, Any]:
    _, cfg = active_policy(db)
    placement = placement_for(db, student.id, cfg)
    profile = StudentProfile.of(student)
    drive = offer.application.drive
    sector = drive.company.sector
    hyp = placement.with_offer(offer.ctc_lpa, drive.company.name, sector)
    ends_season = cfg.is_freezing(offer.ctc_lpa, sector)
    thr = cfg.freeze_at_ctc_lpa
    model = build_model(db, student, cfg)
    applied_to = {a.drive_id for a in student.applications if a.status != AppStatus.withdrawn}

    rows: list[dict[str, Any]] = []
    upcoming = db.scalars(select(Drive).where(Drive.status.in_(UPCOMING), Drive.id != drive.id)).all()
    for d in sorted(upcoming, key=lambda x: -x.ctc_lpa):
        specs = rule_specs(d.rules)
        now = evaluate(profile, specs, cfg, placement, d.ctc_lpa, d.id)
        after = evaluate(profile, specs, cfg, hyp, d.ctc_lpa, d.id)
        if not now.eligible and not after.eligible:
            continue
        tier = cfg.tier_name(d.ctc_lpa)
        lost_reason = next((r.message for r in after.failed if r.code.startswith("P")), None)
        rows.append({
            "drive_id": d.id, "company": d.company.name, "role": d.role, "ctc_lpa": d.ctc_lpa,
            "tier": "PSU" if (d.company.sector or "").upper() == "PSU" else tier,
            "eligible_now": now.eligible, "eligible_after": after.eligible,
            "uses_chance": after.eligible and d.ctc_lpa >= thr, "applied": d.id in applied_to,
            "odds": model.odds(tier), "reason": None if after.eligible else lost_reason,
        })

    used = hyp.attempts_used()
    chances_left = 0 if ends_season else max(0, cfg.upgrade_attempts - used)
    upgrades = sorted((r for r in rows if r["eligible_after"] and r["ctc_lpa"] >= thr),
                      key=lambda r: -r["odds"])[:chances_left]
    better_if_decline = sorted((r for r in rows if r["eligible_now"] and r["ctc_lpa"] > offer.ctc_lpa),
                               key=lambda r: -r["odds"])[:6]

    if ends_season:
        verdict = (f"Accepting {drive.company.name} ({offer.ctc_lpa:g} LPA) ends your season under P3: "
                   f"you'd keep this offer and sit for no more drives.")
    elif chances_left:
        verdict = (f"Accepting keeps you in the season with {chances_left} chance(s) at {thr:g}+ LPA drives. "
                   f"Each application to one uses a chance.")
    else:
        verdict = "Accepting leaves you no chances at higher drives."
    return {
        "offer": {"id": offer.id, "company": drive.company.name, "role": drive.role, "ctc_lpa": offer.ctc_lpa,
                  "tier": cfg.tier_name(offer.ctc_lpa), "sector": sector, "ends_season": ends_season,
                  "deadline": offer.deadline.isoformat() if offer.deadline else None},
        "verdict": verdict,
        "threshold_lpa": thr,
        "chances": {"total": cfg.upgrade_attempts, "left_after_accept": chances_left},
        "drives": rows,
        "summary": {
            "keep": sum(r["eligible_now"] and r["eligible_after"] for r in rows),
            "lose": sum(r["eligible_now"] and not r["eligible_after"] for r in rows),
            "accept_upgrade_odds": p_at_least_one([r["odds"] for r in upgrades]) if upgrades else 0.0,
            "accept_upgrade_targets": [r["company"] for r in upgrades],
            "decline_better_odds": p_at_least_one([r["odds"] for r in better_if_decline]),
            "decline_better_targets": [r["company"] for r in better_if_decline],
        },
        "basis": {
            "tier_rates": model.tier_rates, "personal_factor": model.personal_factor,
            "method": "Odds per drive = this season's selection rate for its tier on completed drives x your "
                      "round clearance rate relative to the batch. Estimates, not promises.",
        },
    }
