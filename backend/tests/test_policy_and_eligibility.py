from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.eligibility import RuleSpec, StudentProfile, check_rule, evaluate
from app.services.policy import Placement, PolicyConfig, Tier, check_policy

CFG = PolicyConfig()
ASHA = StudentProfile(id=1, cgpa=7.8, backlogs=1, branch="ECE", programme="BTECH", grad_year=2026)


def codes(checks: list) -> dict[str, bool]:  # type: ignore[type-arg]
    return {c.code: c.ok for c in checks}


def test_tiers_are_assigned_by_ctc() -> None:
    assert CFG.tier_name(6.75) == "Regular"
    assert CFG.tier_name(10) == "Dream"
    assert CFG.tier_name(19.99) == "Dream"
    assert CFG.tier_name(24) == "Super Dream"


def test_policy_config_validates_tiers() -> None:
    with pytest.raises(ValidationError):
        PolicyConfig(tiers=[Tier(name="Dream", min_ctc_lpa=10)])
    with pytest.raises(ValidationError):
        PolicyConfig(tiers=[Tier(name="A", min_ctc_lpa=0), Tier(name="a", min_ctc_lpa=5)])
    cfg = PolicyConfig(tiers=[Tier(name="High", min_ctc_lpa=15), Tier(name="Base", min_ctc_lpa=0)],
                       freeze_sectors=[" psu ", "PSU"])
    assert [t.name for t in cfg.tiers] == ["Base", "High"]
    assert cfg.freeze_sectors == ["PSU"]


def test_unplaced_student_has_no_policy_checks() -> None:
    assert check_policy(CFG, Placement(), 30) == []


def test_below_threshold_offer_allows_only_higher_drives() -> None:
    placed = Placement((6.75,), ("Cognizant",), ("IT services",))
    assert codes(check_policy(CFG, placed, 9.5, drive_id=1))["P1"] is False      # another Regular drive
    result = codes(check_policy(CFG, placed, 24, drive_id=2))
    assert result["P1"] and result["P6"] and result["P2"]


def test_two_upgrade_chances_then_none() -> None:
    one = Placement((6.75,), ("Cognizant",), ("",), frozenset({10}))
    assert codes(check_policy(CFG, one, 18, drive_id=11))["P6"] is True
    two = Placement((6.75,), ("Cognizant",), ("",), frozenset({10, 11}))
    assert codes(check_policy(CFG, two, 18, drive_id=12))["P6"] is False
    # The drives already applied to stay eligible, so their applications don't flip to "not eligible".
    assert codes(check_policy(CFG, two, 18, drive_id=11))["P6"] is True


@pytest.mark.parametrize(("ctc", "sector"), [(12.0, "Banking"), (24.0, "Semiconductors"), (8.0, "PSU")])
def test_dream_or_psu_offer_ends_the_season(ctc: float, sector: str) -> None:
    checks = check_policy(CFG, Placement((ctc,), ("X",), (sector,)), 40)
    assert [c.code for c in checks] == ["P3"]
    assert checks[0].ok is False


def test_offer_limit() -> None:
    cfg = PolicyConfig(max_offers=1, upgrade_only=False)
    assert codes(check_policy(cfg, Placement((8.0,), ("X",), ("",)), 12))["P2"] is False


def test_rule_messages_explain_the_outcome() -> None:
    r = check_rule(ASHA, RuleSpec("cgpa", ">=", 8.0, id=7))
    assert not r.ok and r.code == "rule:7"
    assert "7.8" in r.message and "8" in r.message
    b = check_rule(ASHA, RuleSpec("backlogs", "<=", 0))
    assert not b.ok and b.fixable
    assert check_rule(ASHA, RuleSpec("branch", "in", ["CSE", "ECE"])).ok


def test_evaluate_combines_rules_and_policy() -> None:
    rules = [RuleSpec("cgpa", ">=", 7.5), RuleSpec("branch", "in", ["ECE", "EEE"])]
    assert evaluate(ASHA, rules, CFG, Placement(), 18).eligible
    decision = evaluate(ASHA, rules, CFG, Placement((12.0,), ("N",), ("",)), 18)
    assert not decision.eligible
    assert [r.code for r in decision.failed] == ["P3"]


def test_describe_lists_rule_codes() -> None:
    found = [r["code"] for r in CFG.describe()]
    assert {"P0", "P1", "P2", "P3", "P5", "P6"} <= set(found)
