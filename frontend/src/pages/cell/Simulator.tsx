import { useState } from "react";
import { api } from "../../api/client";
import type { SimReport } from "../../api/types";
import { Compare } from "../../components/charts";
import { Button, Card, Field, PageHead, useAction } from "../../components/ui";

export function CellSimulator() {
  const [cfg, setCfg] = useState({ candidates: 24, panels: 3, duration_min: 30, overrun_mean: 1.15, no_show_rate: 0.08, trials: 15 });
  const [report, setReport] = useState<SimReport | null>(null);
  const run = useAction(() => api.post<SimReport>("/api/cell/simulate", cfg), { onDone: setReport });
  const num = (k: keyof typeof cfg, v: string) => setCfg({ ...cfg, [k]: Number(v) });
  return (
    <>
      <PageHead title="Drive simulator"
        lead="Replays a drive day with random overruns and no-shows, once with a fixed plan and once with live re-planning." />
      <div className="grid grid-2">
        <Card title="Scenario">
          <div className="form-grid">
            <Field label="Candidates"><input type="number" min={2} max={80} value={cfg.candidates} onChange={(e) => num("candidates", e.target.value)} /></Field>
            <Field label="Panels"><input type="number" min={1} max={10} value={cfg.panels} onChange={(e) => num("panels", e.target.value)} /></Field>
            <Field label="Planned minutes"><input type="number" min={10} max={90} value={cfg.duration_min} onChange={(e) => num("duration_min", e.target.value)} /></Field>
            <Field label="Trials"><input type="number" min={1} max={60} value={cfg.trials} onChange={(e) => num("trials", e.target.value)} /></Field>
          </div>
          <div className="stack" style={{ marginTop: 12 }}>
            <Field label={`Interviews run ${Math.round((cfg.overrun_mean - 1) * 100)}% long on average`}>
              <input type="range" min={0.9} max={1.6} step={0.05} value={cfg.overrun_mean} onChange={(e) => num("overrun_mean", e.target.value)} />
            </Field>
            <Field label={`${Math.round(cfg.no_show_rate * 100)}% of students don't show up`}>
              <input type="range" min={0} max={0.3} step={0.01} value={cfg.no_show_rate} onChange={(e) => num("no_show_rate", e.target.value)} />
            </Field>
            <Button variant="primary" busy={run.isPending} onClick={() => run.mutate(undefined)}>Run simulation</Button>
          </div>
        </Card>
        <Card title="Result" subtitle={report ? `Average over ${report.config.trials} trials with the same random draws for both plans.` : "Run a scenario to compare."}>
          {report ? (
            <div className="stack" style={{ gap: 16 }}>
              <Compare label="Day finishes after" before={report.static.makespan_min} after={report.replanned.makespan_min} unit="min" />
              <Compare label="Average student wait" before={report.static.avg_wait_min} after={report.replanned.avg_wait_min} unit="min" />
              <Compare label="Worst student wait" before={report.static.max_wait_min} after={report.replanned.max_wait_min} unit="min" />
              <Compare label="Panel idle time" before={report.static.panel_idle_min} after={report.replanned.panel_idle_min} unit="min" />
              <p className="small muted">
                Re-planned {report.replanned.replans} times per drive on average. Wait is how long a student waits past their
                original slot. Idle time counts only gaps between a panel's first and last interview, so a panel that takes
                over someone else's student can show a little more idle time while the day still ends earlier.
              </p>
            </div>
          ) : (
            <p className="muted">This is the evaluation for HireFlo: how much live re-planning saves on a messy day.</p>
          )}
        </Card>
      </div>
    </>
  );
}
