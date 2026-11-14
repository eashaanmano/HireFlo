"""Institute placement policy: tiers, the one-offer rule, upgrade attempts and the season freeze.

Policies are encoded once as data (PolicyConfig) and versioned in the database. Each rule has a stable code
(P0, P1, ...) so explanations and the Q&A assistant can cite exactly which rule applied.

Default policy (NIT-style "one offer" rule):
  * an offer below 10 LPA is accepted -> the student may still sit for at most 2 more drives paying 10 LPA or
    more (P6), and only drives in a higher tier (P1);
  * an offer of 10 LPA or more, or any offer from a PSU, ends the student's placement season (P3).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Application, AppStatus, Company, Drive, Offer, OfferStatus, Policy


class Tier(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    min_ctc_lpa: float = Field(ge=0)


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tiers: list[Tier] = Field(
        default_factory=lambda: [
            Tier(name="Regular", min_ctc_lpa=0),
            Tier(name="Dream", min_ctc_lpa=10),
            Tier(name="Super Dream", min_ctc_lpa=20),
        ],
        min_length=1,
    )
    upgrade_only: bool = True                              # P1: once placed, only higher-tier drives
    max_offers: int = Field(default=2, ge=1, le=10)        # P2: total accepted offers allowed
    freeze_at_ctc_lpa: float = Field(default=10, ge=0)     # P3: an offer at or above this ends the season
    freeze_sectors: list[str] = Field(default_factory=lambda: ["PSU"])  # P3: ...and so does any PSU offer
    min_ctc_jump_lpa: float = Field(default=0, ge=0)       # P4: next drive must pay at least this much more
    offer_deadline_hours: int = Field(default=48, ge=1, le=720)  # P5
    upgrade_attempts: int = Field(default=2, ge=0, le=10)  # P6: drives at/above the freeze line after a lower offer

    @model_validator(mode="after")
    def _sorted_tiers(self) -> PolicyConfig:
        self.tiers = sorted(self.tiers, key=lambda t: t.min_ctc_lpa)
        if self.tiers[0].min_ctc_lpa != 0:
            raise ValueError("The lowest tier must start at 0 LPA.")
        names = [t.name.lower() for t in self.tiers]
        if len(set(names)) != len(names):
            raise ValueError("Tier names must be unique.")
        self.freeze_sectors = sorted({s.strip().upper() for s in self.freeze_sectors if s.strip()})
        return self

    def tier_index(self, ctc_lpa: float) -> int:
        idx = 0
        for i, tier in enumerate(self.tiers):
            if ctc_lpa >= tier.min_ctc_lpa:
                idx = i
        return idx

    def tier_name(self, ctc_lpa: float) -> str:
        return self.tiers[self.tier_index(ctc_lpa)].name

    def is_freezing(self, ctc_lpa: float, sector: str | None) -> bool:
        return ctc_lpa >= self.freeze_at_ctc_lpa or (sector or "").strip().upper() in self.freeze_sectors

    def describe(self) -> list[dict[str, str]]:
        """Human-readable rule list, used by the policy page and as Q&A grounding."""
        tiers = ", ".join(f"{t.name} from {t.min_ctc_lpa:g} LPA" for t in self.tiers)
        thr = f"{self.freeze_at_ctc_lpa:g} LPA"
        sectors = " or ".join(self.freeze_sectors)
        rules = [{"code": "P0", "title": "Tiers", "text": f"Drives are tiered by CTC: {tiers}."}]
        if self.upgrade_only:
            rules.append({"code": "P1", "title": "Upgrade only",
                          "text": "After accepting an offer, a student may only sit for drives in a higher tier."})
        rules.append({"code": "P2", "title": "Offer limit",
                      "text": f"A student may accept at most {self.max_offers} offer(s) in total."})
        rules.append({"code": "P3", "title": "Season ends",
                      "text": f"Accepting an offer of {thr} or more"
                              + (f", or any offer from a {sectors} company" if sectors else "")
                              + ", removes the student from further drives."})
        if self.min_ctc_jump_lpa > 0:
            rules.append({"code": "P4", "title": "Minimum jump",
                          "text": f"A new drive must offer at least {self.min_ctc_jump_lpa:g} LPA more "
                                  "than the student's accepted offer."})
        rules.append({"code": "P5", "title": "Offer deadline",
                      "text": f"Offers must be accepted or declined within {self.offer_deadline_hours} hours."})
        rules.append({"code": "P6", "title": "Upgrade chances",
                      "text": f"A student who accepts an offer below {thr} may sit for at most "
                              f"{self.upgrade_attempts} more drive(s) paying {thr} or more."})
        return rules


@dataclass(frozen=True)
class Placement:
    """A student's accepted offers and post-offer attempts, as the policy engine sees them."""

    accepted_ctcs: tuple[float, ...] = ()
    companies: tuple[str, ...] = ()
    sectors: tuple[str, ...] = ()
    attempt_drive_ids: frozenset[int] = field(default_factory=frozenset)

    @property
    def best_ctc(self) -> float | None:
        return max(self.accepted_ctcs) if self.accepted_ctcs else None

    def with_offer(self, ctc: float, company: str, sector: str | None = None) -> Placement:
        return Placement(self.accepted_ctcs + (ctc,), self.companies + (company,),
                         self.sectors + ((sector or ""),), self.attempt_drive_ids)

    def frozen(self, cfg: PolicyConfig) -> bool:
        return any(cfg.is_freezing(c, s) for c, s in zip(self.accepted_ctcs, self._sectors(), strict=True))

    def attempts_used(self, exclude_drive: int | None = None) -> int:
        return len(self.attempt_drive_ids - ({exclude_drive} if exclude_drive is not None else set()))

    def _sectors(self) -> tuple[str, ...]:
        return self.sectors if len(self.sectors) == len(self.accepted_ctcs) else ("",) * len(self.accepted_ctcs)


@dataclass(frozen=True)
class PolicyCheck:
    code: str
    ok: bool
    message: str


def check_policy(cfg: PolicyConfig, placement: Placement, drive_ctc: float,
                 drive_id: int | None = None) -> list[PolicyCheck]:
    """Every policy check that applies to this student for a drive paying `drive_ctc`."""
    best = placement.best_ctc
    if best is None:
        return []
    sectors = placement._sectors()
    i_best = placement.accepted_ctcs.index(best)
    company = placement.companies[i_best] if placement.companies else "an offer"
    thr = cfg.freeze_at_ctc_lpa

    for ctc, sector, name in zip(placement.accepted_ctcs, sectors, placement.companies or ("",) * len(sectors),
                                 strict=True):
        if cfg.is_freezing(ctc, sector):
            why = f"a {sector.upper()} offer" if (sector or "").upper() in cfg.freeze_sectors else \
                f"an offer of {ctc:g} LPA ({thr:g} LPA or more)"
            return [PolicyCheck("P3", False, f"You've accepted {why} from {name}, so P3 ends your placement season.")]

    checks: list[PolicyCheck] = []
    count = len(placement.accepted_ctcs)
    checks.append(PolicyCheck("P2", count < cfg.max_offers,
                              f"You have accepted {count} of {cfg.max_offers} allowed offer(s)."))
    held_tier = cfg.tier_index(best)
    held_name = cfg.tiers[held_tier].name
    if cfg.upgrade_only:
        drive_tier = cfg.tier_index(drive_ctc)
        ok = drive_tier > held_tier
        drive_name = cfg.tiers[drive_tier].name
        msg = (f"This is a {drive_name} drive, above your {held_name} offer from {company}." if ok else
               f"This is a {drive_name} drive; with a {held_name} offer from {company}, P1 only allows higher tiers.")
        checks.append(PolicyCheck("P1", ok, msg))
    if drive_ctc >= thr:
        used = placement.attempts_used(drive_id)
        ok = used < cfg.upgrade_attempts
        left = max(0, cfg.upgrade_attempts - used)
        checks.append(PolicyCheck("P6", ok,
                                  f"You've used {used} of {cfg.upgrade_attempts} chances at {thr:g}+ LPA drives "
                                  f"after your {company} offer" + (f"; {left} left." if ok else "; none left.")))
    if cfg.min_ctc_jump_lpa > 0:
        need = best + cfg.min_ctc_jump_lpa
        checks.append(PolicyCheck("P4", drive_ctc >= need,
                                  f"Drive pays {drive_ctc:g} LPA; P4 needs at least {need:g} LPA."))
    return checks


# ---------------------------------------------------------------- persistence helpers
def active_policy(db: Session) -> tuple[Policy, PolicyConfig]:
    row = db.scalar(select(Policy).where(Policy.active.is_(True)).order_by(Policy.version.desc()))
    if row is None:
        cfg = PolicyConfig()
        row = Policy(version=1, config=cfg.model_dump(), note="Default policy", active=True, created_by="system")
        db.add(row)
        db.flush()
        return row, cfg
    return row, PolicyConfig.model_validate(row.config)


def save_new_version(db: Session, cfg: PolicyConfig, note: str | None, actor: str) -> Policy:
    current, _ = active_policy(db)
    current.active = False
    row = Policy(version=current.version + 1, config=cfg.model_dump(), note=note, active=True, created_by=actor)
    db.add(row)
    db.flush()
    return row


def placements_for_all(db: Session, cfg: PolicyConfig | None = None,
                       student_id: int | None = None) -> dict[int, Placement]:
    """Accepted offers per student, plus the higher drives they applied to after their first acceptance."""
    if cfg is None:
        _, cfg = active_policy(db)
    stmt = (select(Application.student_id, Offer.ctc_lpa, Offer.decided_at, Company.name, Company.sector)
            .join(Offer, Offer.application_id == Application.id)
            .join(Drive, Application.drive_id == Drive.id)
            .join(Company, Drive.company_id == Company.id)
            .where(Offer.status == OfferStatus.accepted))
    if student_id is not None:
        stmt = stmt.where(Application.student_id == student_id)
    rows = db.execute(stmt).all()
    if not rows:
        return {}
    offers: dict[int, list[tuple[float, datetime | None, str, str]]] = defaultdict(list)
    for sid, ctc, decided, name, sector in rows:
        offers[sid].append((ctc, decided, name, sector or ""))

    attempts: dict[int, set[int]] = defaultdict(set)
    app_stmt = (select(Application.student_id, Application.drive_id, Application.created_at)
                .join(Drive, Application.drive_id == Drive.id)
                .where(Application.student_id.in_(list(offers)), Application.status != AppStatus.withdrawn,
                       Drive.ctc_lpa >= cfg.freeze_at_ctc_lpa))
    for sid, drive_id, created in db.execute(app_stmt):
        first = min((d for _, d, _, _ in offers[sid] if d is not None), default=None)
        if first is not None and created is not None and created > first:
            attempts[sid].add(drive_id)

    return {sid: Placement(tuple(o[0] for o in items), tuple(o[2] for o in items), tuple(o[3] for o in items),
                           frozenset(attempts.get(sid, set())))
            for sid, items in offers.items()}


def placement_for(db: Session, student_id: int, cfg: PolicyConfig | None = None) -> Placement:
    return placements_for_all(db, cfg, student_id=student_id).get(student_id, Placement())
