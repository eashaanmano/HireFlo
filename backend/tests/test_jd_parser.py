from __future__ import annotations

import pytest

from app.seed import CATALOG, CompanySpec, jd_for
from app.services.jd_parser import extract_rules, find_skills

BY_NAME = {c.name: c for c in CATALOG}


def rules_of(text: str) -> dict[str, tuple[str, object, float]]:
    return {r.field: (r.op, r.value, r.confidence) for r in extract_rules(text).rules}


@pytest.mark.parametrize("spec", [c for c in CATALOG if c.name != "ARM"], ids=lambda c: c.name)
def test_every_demo_jd_extracts_its_own_criteria(spec: CompanySpec) -> None:
    ext = extract_rules(jd_for(spec))
    r = rules_of(jd_for(spec))
    assert ext.ctc_lpa == spec.ctc
    assert r["cgpa"][:2] == (">=", spec.cgpa)
    assert r["backlogs"][:2] == ("<=", spec.backlogs)
    if spec.branches:
        assert set(r["branch"][1]) == set(spec.branches)  # type: ignore[arg-type]
    else:
        assert "branch" not in r
    assert set(r["programme"][1]) == {"BTECH", "MTECH"}  # type: ignore[arg-type]
    assert r["grad_year"][1] == [2026]


def test_amd_skills() -> None:
    skills = set(extract_rules(jd_for(BY_NAME["AMD"])).skills)
    assert {"verilog", "systemverilog", "computer architecture", "c++", "python", "linux"} <= skills


def test_fuzzy_jd_gets_low_confidence() -> None:
    r = rules_of(jd_for(BY_NAME["ARM"]))
    assert set(r["branch"][1]) == {"CSE", "ECE", "EEE"}  # type: ignore[arg-type]  # "circuit branches"
    assert r["branch"][2] < 0.8
    assert r["cgpa"][1] == 7.5 and r["cgpa"][2] <= 0.5     # 75% aggregate converted


def test_handwritten_phrasings() -> None:
    r = rules_of("Eligibility: Mechanical or Civil. Up to 2 backlogs permitted. Minimum 6.8 CGPA throughout.")
    assert r["backlogs"][:2] == ("<=", 2)
    assert r["cgpa"][1] == 6.8
    r2 = rules_of("Students from Mechanical, Electrical and Production streams. No standing arrears.")
    assert set(r2["branch"][1]) == {"ME", "EEE", "PE"}  # type: ignore[arg-type]
    assert r2["backlogs"][:2] == ("<=", 0)


def test_skill_matching_avoids_false_positives() -> None:
    assert "c" not in find_skills("Experience with React and Docker.")
    assert "c" in find_skills("Languages: C, C++ and Python.")
    assert "java" in find_skills("Java and Spring")
    assert "javascript" not in find_skills("Java only")
