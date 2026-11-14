"""Drive-day simulator: replays a drive with random overruns and no-shows, with and without live re-planning.

Both policies start from the same CP-SAT schedule and face the same random draws per trial, so the
difference in the metrics is caused by re-planning alone. This is the evaluation story for DriveOps.
"""
from __future__ import annotations

import random
import statistics
from dataclasses import asdict, dataclass, field

from .scheduler import REPLAN_WEIGHTS, Assignment, Candidate, PanelSpec, SchedulingError, schedule


@dataclass(frozen=True)
class SimConfig:
    candidates: int = 24
    panels: int = 3
    duration_min: int = 30
    overrun_mean: float = 1.15         # actual / planned duration, mean
    overrun_sd: float = 0.30
    no_show_rate: float = 0.08
    arrive_early_min: int = 10         # students arrive this long before their slot
    grace_min: int = 10                # a no-show is detected this long after the planned start
    delay_threshold_min: int = 10      # lateness that triggers a re-plan
    notice_min: int = 15               # a student told to come earlier (live ETA update) is there this soon
    trials: int = 20
    seed: int = 7
    solver_time_limit_sec: float = 0.5


@dataclass
class PolicyMetrics:
    makespan_min: float = 0
    avg_wait_min: float = 0          # time a present student waits beyond their planned start
    max_wait_min: float = 0
    panel_idle_min: float = 0        # idle gaps between a panel's first and last interview
    replans: float = 0


@dataclass
class SimReport:
    config: SimConfig
    static: PolicyMetrics
    replanned: PolicyMetrics
    improvement: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {"config": asdict(self.config), "static": asdict(self.static),
                "replanned": asdict(self.replanned), "improvement": self.improvement}


@dataclass
class _Trial:
    actual: dict[int, int]          # key -> actual duration
    no_show: set[int]


def _draw(cfg: SimConfig, rng: random.Random, keys: list[int]) -> _Trial:
    actual = {}
    for k in keys:
        factor = min(2.2, max(0.6, rng.gauss(cfg.overrun_mean, cfg.overrun_sd)))
        actual[k] = max(5, round(cfg.duration_min * factor))
    no_show = {k for k in keys if rng.random() < cfg.no_show_rate}
    return _Trial(actual, no_show)


def _run(cfg: SimConfig, plan: list[Assignment], trial: _Trial, replan: bool) -> PolicyMetrics:
    original = {a.key: a for a in plan}
    current = {a.key: a for a in plan}
    arrival = {k: max(0, a.start - cfg.arrive_early_min) for k, a in original.items()}
    panel_ids = sorted({a.panel_id for a in plan})
    free_at = {p: 0 for p in panel_ids}
    done: set[int] = set()
    detected_no_show: set[int] = set()
    waits: list[float] = []
    busy_spans: dict[int, list[tuple[int, int]]] = {p: [] for p in panel_ids}
    replans = 0
    pending: list[int] = []          # times at which a re-plan is due (an overrun or no-show was noticed)

    def queue(p: int) -> list[Assignment]:
        return sorted((a for k, a in current.items() if a.panel_id == p and k not in done
                       and k not in detected_no_show), key=lambda a: a.start)

    def do_replan(now: int) -> None:
        nonlocal current, replans
        remaining = [k for k in current if k not in done and k not in detected_no_show]
        if not remaining:
            return
        # Students already on site can go any time; others can be called in, but need notice_min to arrive.
        reach = {k: arrival[k] if arrival[k] <= now else min(arrival[k], now + cfg.notice_min) for k in remaining}
        cands = [Candidate(key=k, earliest=reach[k], preferred_panel=current[k].panel_id,
                           preferred_start=max(now, current[k].start), priority=1 if arrival[k] <= now else 0)
                 for k in remaining]
        specs = [PanelSpec(id=p, available_from=max(now, free_at[p])) for p in panel_ids]
        try:
            result = schedule(cands, specs, cfg.duration_min, weights=REPLAN_WEIGHTS,
                              time_limit_sec=cfg.solver_time_limit_sec, workers=4)
        except SchedulingError:
            return
        for a in result.assignments:
            current[a.key] = a
            # the live ETA tells a moved-up student to come earlier; they arrive early as usual, if they can
            arrival[a.key] = min(arrival[a.key], max(reach[a.key], a.start - cfg.arrive_early_min))
        replans += 1

    while True:
        options = [(free_at[p], p) for p in panel_ids if queue(p)]
        if not options:
            break
        now, p = min(options)
        if pending and min(pending) <= now:
            # Re-plan on the shared clock: every panel is where it really is at that moment, not later.
            t = min(pending)
            pending = [x for x in pending if x > t]
            do_replan(t)
            continue
        nxt = queue(p)[0]
        k = nxt.key
        if k in trial.no_show:
            detect = max(now, nxt.start + cfg.grace_min)
            free_at[p] = detect  # panel waits for the student until the no-show is detected
            detected_no_show.add(k)
            if replan:
                pending.append(detect)
            continue
        start = max(now, arrival[k])
        end = start + trial.actual[k]
        free_at[p] = end
        done.add(k)
        busy_spans[p].append((start, end))
        waits.append(max(0, start - original[k].start))
        if replan and end - current[k].end >= cfg.delay_threshold_min:
            pending.append(end)

    idle = 0
    for spans in busy_spans.values():
        spans.sort()
        idle += sum(max(0, b[0] - a[1]) for a, b in zip(spans, spans[1:], strict=False))
    makespan = max((e for spans in busy_spans.values() for _, e in spans), default=0)
    return PolicyMetrics(makespan_min=makespan, avg_wait_min=statistics.fmean(waits) if waits else 0,
                         max_wait_min=max(waits, default=0), panel_idle_min=idle, replans=replans)


def _mean(items: list[PolicyMetrics]) -> PolicyMetrics:
    return PolicyMetrics(**{f: round(statistics.fmean(getattr(m, f) for m in items), 1)
                            for f in PolicyMetrics.__dataclass_fields__})


def run_simulation(cfg: SimConfig) -> SimReport:
    rng = random.Random(cfg.seed)
    keys = list(range(1, cfg.candidates + 1))
    base = schedule([Candidate(key=k) for k in keys], [PanelSpec(id=p) for p in range(1, cfg.panels + 1)],
                    cfg.duration_min, time_limit_sec=max(cfg.solver_time_limit_sec, 2.0), workers=4).assignments
    static_runs: list[PolicyMetrics] = []
    replan_runs: list[PolicyMetrics] = []
    for _ in range(cfg.trials):
        trial = _draw(cfg, rng, keys)
        static_runs.append(_run(cfg, base, trial, replan=False))
        replan_runs.append(_run(cfg, base, trial, replan=True))
    s, r = _mean(static_runs), _mean(replan_runs)

    def pct(a: float, b: float) -> float:
        return round(100 * (a - b) / a, 1) if a else 0.0

    return SimReport(cfg, s, r, {
        "makespan_pct": pct(s.makespan_min, r.makespan_min),
        "avg_wait_pct": pct(s.avg_wait_min, r.avg_wait_min),
        "panel_idle_pct": pct(s.panel_idle_min, r.panel_idle_min),
    })
