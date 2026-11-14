"""Fairness audit for a drive's eligibility rules.

For the placement cell: who each rule excludes and from which branches, who misses a cutoff by a hair, how the pool
changes as the CGPA cutoff moves, and whether any accepted branch falls below the four-fifths rule (a branch's
eligibility rate under 80% of the best branch's rate). Students the placement policy already excludes are counted
separately so rules aren't blamed for policy outcomes.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Drive, Student
from .eligibility import FIELD_LABELS, RuleSpec, StudentProfile, check_rule, rule_specs
from .insights import pool_placements
from .policy import Placement, active_policy, check_policy

NEAR_MISS = 0.25            # CGPA points
FOUR_FIFTHS = 0.8
MIN_BRANCH_SIZE = 5         # don't flag tiny groups; the ratio is noise there
SWEEP_STEP = 0.1
SWEEP_HALF_WIDTH = 0.5


def rule_label(r: RuleSpec) -> str:
    label = FIELD_LABELS.get(r.field, r.field)
    if r.op == "in":
        return f"{label} in {', '.join(str(v) for v in (r.value or []))}"
    if r.field == "backlogs" and r.op == "<=":
        return f"At most {int(r.value)} active backlog(s)"
    return f"{label} {r.op} {r.value:g}" if isinstance(r.value, float) else f"{label} {r.op} {r.value}"


def _passes(p: StudentProfile, rules: list[RuleSpec]) -> bool:
    return all(check_rule(p, r).ok for r in rules)


def drive_fairness(db: Session, drive: Drive) -> dict[str, Any]:
    _, cfg = active_policy(db)
    profiles, placements = pool_placements(db)
    rules = rule_specs(drive.rules)
    branch_rule = next((r for r in rules if r.field == "branch" and r.op == "in"), None)
    allowed = {str(v).upper() for v in branch_rule.value} if branch_rule else None

    policy_out = 0
    pool: list[StudentProfile] = []
    for p in profiles:
        checks = check_policy(cfg, placements.get(p.id) or Placement(), drive.ctc_lpa, drive.id)
        if any(not c.ok for c in checks):
            policy_out += 1
        else:
            pool.append(p)

    per_rule: list[dict[str, Any]] = []
    failed_by: dict[int, list[int]] = defaultdict(list)    # student -> indexes of failed rules
    for p in pool:
        for i, r in enumerate(rules):
            if not check_rule(p, r).ok:
                failed_by[p.id].append(i)
    for i, r in enumerate(rules):
        hit = [p for p in pool if i in failed_by[p.id]]
        by_branch: dict[str, int] = defaultdict(int)
        for p in hit:
            by_branch[p.branch] += 1
        per_rule.append({
            "id": r.id, "field": r.field, "label": rule_label(r), "excluded": len(hit),
            "sole": sum(1 for p in hit if failed_by[p.id] == [i]),
            "by_branch": [{"branch": b, "count": n} for b, n in sorted(by_branch.items(), key=lambda kv: -kv[1])],
        })

    # Branch eligibility rates (only branches the drive accepts, so the branch rule itself isn't "disparity").
    branches: dict[str, dict[str, int]] = defaultdict(lambda: {"students": 0, "eligible": 0})
    for p in pool:
        if allowed is not None and p.branch.upper() not in allowed:
            continue
        b = branches[p.branch]
        b["students"] += 1
        b["eligible"] += not failed_by[p.id]
    rates = {k: v["eligible"] / v["students"] for k, v in branches.items() if v["students"]}
    sized = {k: rate for k, rate in rates.items() if branches[k]["students"] >= MIN_BRANCH_SIZE}
    best = max(sized.values(), default=0.0)
    branch_rows = [{"branch": k, "students": v["students"], "eligible": v["eligible"],
                    "rate": round(100 * rates.get(k, 0.0), 1),
                    "ratio": round(rates[k] / best, 2) if best and k in sized else None}
                   for k, v in sorted(branches.items(), key=lambda kv: -kv[1]["students"])]

    # CGPA near misses and a cutoff sweep.
    cg_index = next((i for i, r in enumerate(rules) if r.field == "cgpa" and r.op == ">="), None)
    near: list[dict[str, Any]] = []
    sweep: list[dict[str, Any]] = []
    if cg_index is not None:
        cutoff = float(rules[cg_index].value)
        others = [r for j, r in enumerate(rules) if j != cg_index]
        names = {sid: (name, roll)
                 for sid, name, roll in db.execute(select(Student.id, Student.name, Student.roll_no))}
        near = sorted(({"student_id": p.id, "name": names.get(p.id, ("", ""))[0],
                        "roll_no": names.get(p.id, ("", ""))[1], "branch": p.branch, "cgpa": p.cgpa,
                        "gap": round(cutoff - p.cgpa, 2)}
                       for p in pool if failed_by[p.id] == [cg_index] and cutoff - p.cgpa <= NEAR_MISS),
                      key=lambda x: x["gap"])
        steps = round(SWEEP_HALF_WIDTH / SWEEP_STEP)
        ok_others = [p for p in pool if _passes(p, others)]
        for k in range(-steps, steps + 1):
            c = round(cutoff + k * SWEEP_STEP, 2)
            sweep.append({"cutoff": c, "eligible": sum(p.cgpa >= c for p in ok_others), "current": k == 0})

    flags: list[dict[str, str]] = []
    for row in branch_rows:
        if row["ratio"] is not None and row["ratio"] < FOUR_FIFTHS:
            flags.append({"level": "warn", "text": (
                f"{row['branch']}: {row['rate']}% eligible, {row['ratio']:.2f}x the best branch. Below the "
                f"four-fifths rule; check whether a rule hits this branch harder than intended.")})
    if near:
        tight = sum(1 for n in near if n["gap"] <= 0.1)
        if tight:
            lower = round(float(rules[cg_index].value) - 0.1, 2)  # type: ignore[index]
            flags.append({"level": "info", "text": (
                f"{tight} student(s) miss the CGPA cutoff by 0.1 or less and pass every other rule. "
                f"A cutoff of {lower:g} would include them.")})
    for r in per_rule:
        if r["field"] == "backlogs" and r["sole"]:
            flags.append({"level": "info", "text": (
                f"{r['sole']} student(s) are out only because of backlogs, which they can clear. "
                "Worth telling them the deadline.")})
    if not flags:
        flags.append({"level": "ok", "text": "No branch falls below the four-fifths rule and no cutoff is "
                                              "excluding a cluster of near misses."})

    eligible = sum(1 for p in pool if not failed_by[p.id])
    return {
        "drive": {"id": drive.id, "company": drive.company.name, "role": drive.role, "ctc_lpa": drive.ctc_lpa},
        "totals": {"students": len(profiles), "policy_excluded": policy_out, "considered": len(pool),
                   "eligible": eligible, "rule_excluded": len(pool) - eligible},
        "rules": per_rule,
        "branches": branch_rows,
        "near_miss": near,
        "near_miss_margin": NEAR_MISS,
        "sweep": sweep,
        "flags": flags,
    }


