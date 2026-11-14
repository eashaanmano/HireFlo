from __future__ import annotations

import pytest

from app.services.scheduler import Candidate, PanelSpec, SchedulingError, schedule, validate
from app.services.simulator import SimConfig, run_simulation


def test_every_candidate_gets_one_conflict_free_slot() -> None:
    cands = [Candidate(key=i) for i in range(1, 13)]
    panels = [PanelSpec(id=1), PanelSpec(id=2), PanelSpec(id=3)]
    result = schedule(cands, panels, 30, time_limit_sec=5)
    assert validate(result.assignments, cands, panels) == []
    assert result.makespan == 4 * 30  # 12 candidates over 3 panels, back to back
    assert {a.panel_id for a in result.assignments} == {1, 2, 3}


def test_student_commitments_and_arrivals_are_respected() -> None:
    cands = [Candidate(key=1, busy=((0, 60),)), Candidate(key=2, earliest=45), Candidate(key=3)]
    panels = [PanelSpec(id=1)]
    result = schedule(cands, panels, 20, time_limit_sec=5)
    assert validate(result.assignments, cands, panels) == []
    by = result.by_key()
    assert by[1].start >= 60
    assert by[2].start >= 45


def test_panel_blocked_time_and_availability() -> None:
    cands = [Candidate(key=i) for i in range(1, 5)]
    panels = [PanelSpec(id=1, blocked=((0, 40),)), PanelSpec(id=2, available_from=15)]
    result = schedule(cands, panels, 20, time_limit_sec=5)
    assert validate(result.assignments, cands, panels) == []
    for a in result.assignments:
        if a.panel_id == 1:
            assert a.start >= 40
        else:
            assert a.start >= 15


def test_replan_prefers_stability() -> None:
    # Panel 1 is late (free at 40); panel 2 is free now. Only the candidates who need to move should move.
    cands = [Candidate(key=1, preferred_panel=1, preferred_start=0), Candidate(key=2, preferred_panel=1,
             preferred_start=30), Candidate(key=3, preferred_panel=2, preferred_start=0)]
    panels = [PanelSpec(id=1, available_from=40), PanelSpec(id=2)]
    result = schedule(cands, panels, 30, time_limit_sec=5)
    assert validate(result.assignments, cands, panels) == []
    assert result.makespan <= 70
    assert result.by_key()[3].panel_id == 2


def test_errors() -> None:
    with pytest.raises(SchedulingError):
        schedule([Candidate(key=1)], [], 30)
    with pytest.raises(SchedulingError):
        schedule([Candidate(key=1), Candidate(key=1)], [PanelSpec(id=1)], 30)
    assert schedule([], [PanelSpec(id=1)], 30).assignments == []


def test_simulator_report_shape() -> None:
    report = run_simulation(SimConfig(candidates=9, panels=3, trials=3, solver_time_limit_sec=0.3)).as_dict()
    assert set(report) == {"config", "static", "replanned", "improvement"}
    assert report["replanned"]["makespan_min"] > 0  # type: ignore[index]
    assert "avg_wait_pct" in report["improvement"]  # type: ignore[operator]


def test_replanning_moves_work_off_an_overrunning_panel() -> None:
    """Regression: re-plans used to run at the overrunning interview's *end*, pushing every other panel's work
    after it, so re-planning looked worse than the fixed plan. Panel 1's first interview runs 45 min long;
    re-planning should hand one of its students to panel 2 and finish earlier with less waiting."""
    from app.services.scheduler import Assignment
    from app.services.simulator import _run, _Trial

    cfg = SimConfig(duration_min=30, solver_time_limit_sec=2.0)
    plan = [Assignment(k, 1 if k <= 4 else 2, ((k - 1) % 4) * 30, ((k - 1) % 4) * 30 + 30) for k in range(1, 9)]
    actual = {k: 30 for k in range(1, 9)} | {1: 75}
    fixed = _run(cfg, plan, _Trial(actual, set()), replan=False)
    replanned = _run(cfg, plan, _Trial(actual, set()), replan=True)
    assert fixed.makespan_min == 165
    assert replanned.replans >= 1
    assert replanned.makespan_min <= 150
    assert replanned.avg_wait_min < fixed.avg_wait_min
