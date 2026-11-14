"""Interview scheduling with Google OR-Tools CP-SAT.

The solver works in integer minutes relative to an origin time, so it has no knowledge of the database.
The same model serves three jobs:

* first schedule of a round (no preferences),
* live re-planning when a panel overruns or a student doesn't show (origin = now, with preferences so the
  new plan moves as few students as possible),
* incremental placement of a student who just cleared the previous round.

Hard constraints
  - every candidate gets exactly one panel of the round;
  - a panel interviews one candidate at a time and respects its blocked time (in-progress, breaks, slots
    already fixed);
  - a candidate can't start before their earliest time (arrival, previous round + buffer);
  - a candidate's slot can't overlap their other commitments (other rounds, other drives the same day);
  - optional working windows (multi-day drives): every interview lies entirely inside one window, so nothing
    runs through lunch, past closing time or overnight. The windows become the domain of each start variable,
    which is both the tightest encoding and the cheapest for the solver.
Objective (lexicographic by weight): finish the day early, start people early, and stay close to the
previous plan (fewer panel changes and smaller time shifts).
"""
from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

Interval = tuple[int, int]  # [start, end) in minutes from origin


class SchedulingError(Exception):
    pass


@dataclass(frozen=True)
class Candidate:
    key: int                                   # application id (or any unique id)
    earliest: int = 0
    busy: tuple[Interval, ...] = ()            # other commitments of this student
    preferred_panel: int | None = None         # current panel, for stability during re-plans
    preferred_start: int | None = None
    priority: int = 0                          # >0: already on site, may be pulled forward in a re-plan


@dataclass(frozen=True)
class PanelSpec:
    id: int
    available_from: int = 0
    blocked: tuple[Interval, ...] = ()


@dataclass(frozen=True)
class Assignment:
    key: int
    panel_id: int
    start: int
    end: int


@dataclass
class ScheduleResult:
    assignments: list[Assignment] = field(default_factory=list)
    makespan: int = 0
    optimal: bool = False
    solve_ms: int = 0

    def by_key(self) -> dict[int, Assignment]:
        return {a.key: a for a in self.assignments}


@dataclass(frozen=True)
class Weights:
    makespan: int = 100
    start: int = 1
    panel_change: int = 25
    shift: int = 1          # per minute moved from the communicated time, either way
    late: int = 4           # extra per minute moved *later* (waiting hurts more than being called early)
    max_late: int = 30      # per minute of the single worst delay, so nobody absorbs everyone's slack


# Re-plans happen with students already waiting: an earlier finish matters, but not at any student's expense.
# One minute off the day is worth about five student-minutes of extra waiting, not a hundred.
REPLAN_WEIGHTS = Weights(makespan=20)


def _horizon(candidates: Sequence[Candidate], panels: Sequence[PanelSpec], duration: int) -> int:
    latest = max([c.earliest for c in candidates] + [p.available_from for p in panels] + [0])
    busy_end = max([e for c in candidates for _, e in c.busy] + [e for p in panels for _, e in p.blocked] + [0])
    # Worst case: everything runs serially after the latest constraint ends.
    return max(latest, busy_end) + duration * (len(candidates) + 1) + 60


def _start_domain(lo: int, hi: int, duration: int, working: Sequence[Interval] | None) -> list[list[int]]:
    """Feasible start minutes in [lo, hi], restricted to starts whose interview fits inside one window."""
    if working is None:
        return [[lo, hi]] if lo <= hi else []
    out = []
    for ws, we in sorted(working):
        a, b = max(lo, ws), min(hi, we - duration)
        if a <= b:
            out.append([a, b])
    return out


def window_capacity(working: Sequence[Interval], duration: int, panels: int, earliest: int = 0) -> int:
    """How many interviews of this length the panels can hold in the windows (an upper bound)."""
    return panels * sum(max(0, (we - max(ws, earliest)) // duration) for ws, we in working)


def schedule(candidates: Sequence[Candidate], panels: Sequence[PanelSpec], duration: int, *,
             weights: Weights | None = None, time_limit_sec: float = 5.0, workers: int = 8,
             working: Sequence[Interval] | None = None) -> ScheduleResult:
    """Assign every candidate a panel and start minute. Raises SchedulingError if impossible.

    `working`, if given, lists the [start, end) windows interviews may use (e.g. 09:00-13:00 and 14:00-18:00 on
    each of the next three days). Without it the day is open-ended, as before.
    """
    if duration <= 0:
        raise SchedulingError("Interview duration must be positive.")
    if not candidates:
        return ScheduleResult(optimal=True)
    if not panels:
        raise SchedulingError("This round has no panels. Add at least one panel first.")
    keys = [c.key for c in candidates]
    if len(set(keys)) != len(keys):
        raise SchedulingError("Duplicate candidate keys.")

    w = weights or Weights()
    if working is not None:
        working = sorted((ws, we) for ws, we in working if we - ws >= duration)
        if not working:
            raise SchedulingError(f"No working window is long enough for a {duration}-minute interview.")
        if window_capacity(working, duration, len(panels)) < len(candidates):
            raise SchedulingError(
                f"{len(candidates)} interviews of {duration} min don't fit in the working hours with "
                f"{len(panels)} panel(s). Allow more days, longer hours or add a panel.")
        horizon = max(we for _, we in working)
    else:
        horizon = _horizon(candidates, panels, duration)
    model = cp_model.CpModel()

    panel_intervals: dict[int, list[cp_model.IntervalVar]] = {p.id: [] for p in panels}
    for p in panels:
        for i, (bs, be) in enumerate(p.blocked):
            if be > bs:
                panel_intervals[p.id].append(model.new_fixed_size_interval_var(bs, be - bs, f"pb{p.id}_{i}"))

    starts: dict[int, cp_model.IntVar] = {}
    choice: dict[tuple[int, int], cp_model.IntVar] = {}
    ends: list[cp_model.LinearExprT] = []
    objective_terms: list[cp_model.LinearExprT] = []
    lates: list[cp_model.IntVar] = []

    for c in candidates:
        dom = _start_domain(max(0, c.earliest), horizon - duration, duration, working)
        if not dom:
            raise SchedulingError(f"Candidate {c.key} cannot fit in the planning horizon.")
        s = model.new_int_var_from_domain(cp_model.Domain.from_intervals(dom), f"s{c.key}")
        starts[c.key] = s
        ends.append(s + duration)
        own = model.new_fixed_size_interval_var(s, duration, f"iv{c.key}")

        # Student can't be in two places.
        busy_ivs = [model.new_fixed_size_interval_var(bs, be - bs, f"cb{c.key}_{i}")
                    for i, (bs, be) in enumerate(c.busy) if be > bs]
        if busy_ivs:
            model.add_no_overlap([own, *busy_ivs])

        lits: list[cp_model.IntVar] = []
        for p in panels:
            lit = model.new_bool_var(f"x{c.key}_{p.id}")
            lits.append(lit)
            choice[(c.key, p.id)] = lit
            panel_intervals[p.id].append(
                model.new_optional_fixed_size_interval_var(s, duration, lit, f"o{c.key}_{p.id}")
            )
            if p.available_from > c.earliest:
                model.add(s >= p.available_from).only_enforce_if(lit)
            if c.preferred_panel is not None and p.id != c.preferred_panel:
                objective_terms.append(w.panel_change * lit)
        model.add_exactly_one(lits)

        # First schedules pull everyone early. During re-plans only people already on site are pulled
        # forward; everyone else is held near their communicated time by the shift penalty below.
        if c.preferred_start is None:
            start_weight = w.start
        else:
            start_weight = w.start * 3 if c.priority > 0 else 0
        if start_weight:
            objective_terms.append(start_weight * s)
        if c.preferred_start is not None:
            dev = model.new_int_var(0, horizon, f"d{c.key}")
            model.add_abs_equality(dev, s - c.preferred_start)
            objective_terms.append(w.shift * dev)
            late = model.new_int_var(0, horizon, f"l{c.key}")
            model.add(late >= s - c.preferred_start)
            objective_terms.append(w.late * late)
            lates.append(late)
            # Start the search from the current plan, so a short time limit still gives a sensible answer.
            model.add_hint(s, min(max(c.preferred_start, dom[0][0]), dom[-1][1]))
            if c.preferred_panel is not None:
                for p in panels:
                    model.add_hint(choice[(c.key, p.id)], int(p.id == c.preferred_panel))

    for ivs in panel_intervals.values():
        if len(ivs) > 1:
            model.add_no_overlap(ivs)

    if lates:
        worst = model.new_int_var(0, horizon, "worst_late")
        model.add_max_equality(worst, lates)
        objective_terms.append(w.max_late * worst)

    makespan = model.new_int_var(0, horizon, "makespan")
    model.add_max_equality(makespan, ends)
    objective_terms.append(w.makespan * makespan)
    model.minimize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_sec
    solver.parameters.num_workers = workers
    t0 = time.perf_counter()
    status = solver.solve(model)
    elapsed = int((time.perf_counter() - t0) * 1000)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise SchedulingError("No conflict-free schedule exists with these panels and constraints.")

    assignments: list[Assignment] = []
    for c in candidates:
        start = int(solver.value(starts[c.key]))
        panel_id = next(p.id for p in panels if solver.boolean_value(choice[(c.key, p.id)]))
        assignments.append(Assignment(c.key, panel_id, start, start + duration))
    assignments.sort(key=lambda a: (a.start, a.panel_id))
    return ScheduleResult(assignments=assignments, makespan=int(solver.value(makespan)),
                          optimal=status == cp_model.OPTIMAL, solve_ms=elapsed)


def validate(assignments: Sequence[Assignment], candidates: Sequence[Candidate],
             panels: Sequence[PanelSpec], working: Sequence[Interval] | None = None) -> list[str]:
    """Independent checker for every hard constraint. Used by tests and before persisting a plan."""
    problems: list[str] = []
    cand = {c.key: c for c in candidates}
    pan = {p.id: p for p in panels}
    if {a.key for a in assignments} != set(cand):
        problems.append("not every candidate was scheduled exactly once")
    by_panel: dict[int, list[Assignment]] = {}
    for a in assignments:
        c = cand.get(a.key)
        p = pan.get(a.panel_id)
        if c is None or p is None:
            problems.append(f"unknown candidate or panel in {a}")
            continue
        if a.start < c.earliest:
            problems.append(f"candidate {a.key} starts before earliest {c.earliest}")
        if a.start < p.available_from:
            problems.append(f"panel {p.id} used before it is available")
        if working is not None and not any(ws <= a.start and a.end <= we for ws, we in working):
            problems.append(f"candidate {a.key} is outside working hours ({a.start}-{a.end})")
        for bs, be in c.busy:
            if a.start < be and bs < a.end:
                problems.append(f"candidate {a.key} overlaps a commitment {bs}-{be}")
        for bs, be in p.blocked:
            if a.start < be and bs < a.end:
                problems.append(f"panel {p.id} double-booked against blocked {bs}-{be}")
        by_panel.setdefault(a.panel_id, []).append(a)
    for p_id, items in by_panel.items():
        items.sort(key=lambda a: a.start)
        for x, y in zip(items, items[1:], strict=False):
            if y.start < x.end:
                problems.append(f"panel {p_id} overlap between {x.key} and {y.key}")
    return problems
