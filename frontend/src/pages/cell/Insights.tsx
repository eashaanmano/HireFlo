import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, download } from "../../api/client";
import { Link } from "react-router-dom";
import type { Coverage, EarlyWarning, PlacementForecast, Retrospective, SeasonAnalytics, Stats } from "../../api/types";
import { HBars, VizCard } from "../../components/charts";
import { DataTable, type Column } from "../../components/DataTable";
import { BarChart, LineChart, MonthCalendar, tierColor, type CalEvent } from "../../components/viz";
import { Badge, Button, Card, ErrorBox, Loading, PageHead } from "../../components/ui";
import { X } from "lucide-react";
import { plural } from "../../lib/format";

const WARN_COLUMNS: Column<EarlyWarning>[] = [
  { key: "name", header: "Student", pinned: true, value: (r) => r.name,
    render: (r) => <><strong>{r.name}</strong> <span className="small faint">{r.roll_no}</span></> },
  { key: "roll_no", header: "Roll no", value: (r) => r.roll_no, hidden: true },
  { key: "branch", header: "Branch", value: (r) => r.branch, filter: true },
  { key: "cgpa", header: "CGPA", value: (r) => r.cgpa, align: "right", render: (r) => r.cgpa.toFixed(2) },
  { key: "backlogs", header: "Backlogs", value: (r) => r.backlogs, align: "right", hidden: true },
  { key: "chance", header: "Chance of an offer", value: (r) => Math.round(r.chance * 100), align: "right",
    render: (r) => <>{Math.round(r.chance * 100)}% <span className="small faint">({r.eligible_upcoming} drives)</span></> },
  { key: "severity", header: "Severity", value: (r) => r.severity, filter: true,
    render: (r) => <Badge tone={r.severity === "high" ? "bad" : "warn"}>{r.severity}</Badge> },
  { key: "reasons", header: "Why", value: (r) => r.reasons.join("; "), sortable: false,
    render: (r) => <span className="small">{r.reasons.join("; ")}</span> },
  { key: "suggestion", header: "Suggested", value: (r) => r.suggestion ?? "", filter: true,
    render: (r) => r.suggestion ? <Badge tone="info">{r.suggestion}</Badge> : "—" },
];

export function CellInsights() {
  const stats = useQuery({ queryKey: ["cell", "stats"], queryFn: () => api.get<Stats>("/api/cell/stats") });
  const coverage = useQuery({ queryKey: ["cell", "coverage"], queryFn: () => api.get<Coverage>("/api/cell/coverage") });
  const risk = useQuery({ queryKey: ["cell", "early-warning"], queryFn: () => api.get<EarlyWarning[]>("/api/cell/early-warning") });
  const forecast = useQuery({ queryKey: ["cell", "forecast"], queryFn: () => api.get<PlacementForecast>("/api/cell/forecast") });
  const season = useQuery({ queryKey: ["cell", "analytics"], queryFn: () => api.get<SeasonAnalytics>("/api/cell/analytics") });
  const cal = useQuery({ queryKey: ["cell", "calendar"], queryFn: () => api.get<CalEvent[]>("/api/cell/calendar") });
  const [branch, setBranch] = useState<string | null>(null);
  if (stats.isLoading || coverage.isLoading) return <Loading />;
  if (stats.error || !stats.data) return <ErrorBox error={stats.error} />;
  const s = stats.data;
  const c = coverage.data;
  return (
    <>
      <PageHead title="Insights" lead="Who's being left out and who needs help, before it's too late in the season."
        right={<Button onClick={() => void download("/api/cell/reports/placements.csv", "placements.csv")}>Export placement report</Button>} />
      <div className="kpi-grid">
        <div className="kpi-tile accent"><div className="v">{s.placed_pct}%</div><div className="l">{s.placed} of {s.students} placed</div></div>
        {season.data && (
          <>
            <div className="kpi-tile"><div className="v">{season.data.kpis.offers_accepted}</div><div className="l">offers accepted</div></div>
            <div className="kpi-tile"><div className="v">{season.data.kpis.offers_pending}</div><div className="l">offers awaiting a decision</div></div>
            <div className="kpi-tile"><div className="v">{season.data.kpis.median_ctc ?? "—"}</div><div className="l">median accepted CTC (LPA)</div></div>
            <div className="kpi-tile"><div className="v">{season.data.kpis.max_ctc ?? "—"}</div><div className="l">highest accepted CTC (LPA)</div></div>
            <div className="kpi-tile"><div className="v">{season.data.kpis.season_over}</div><div className="l">season complete (≥10 LPA or PSU)</div></div>
            <div className="kpi-tile"><div className="v">{season.data.kpis.still_in_play}</div><div className="l">placed, still using upgrade chances</div></div>
          </>
        )}
        <div className="kpi-tile"><div className="v">{c?.uncovered ?? "—"}</div><div className="l">unplaced with no eligible drive</div></div>
        {forecast.data && (
          <div className="kpi-tile"><div className="v">{forecast.data.totals.forecast_pct}%</div><div className="l">forecast placed by season end</div></div>
        )}
        <div className="kpi-tile"><div className="v">{risk.data?.length ?? "—"}</div><div className="l">early-warning flags</div></div>
      </div>
      {season.data && <SeasonCharts a={season.data} />}
      {cal.data && (
        <Card title="Season calendar" subtitle="Application deadlines, drive days and pending offer deadlines across every drive." className="span-2">
          <MonthCalendar events={cal.data} />
        </Card>
      )}
      <div className="grid grid-2" style={{ marginTop: 16 }}>
        <VizCard title="Coverage gaps" subtitle="Unplaced students no upcoming drive is open to. Click a branch to filter the early-warning list."
          rows={c?.by_branch ?? []} filename="coverage_gaps"
          columns={[{ key: "branch", header: "Branch", value: (r) => r.branch }, { key: "students", header: "Uncovered students", value: (r) => r.students, align: "right" }]}>
          {c && c.by_branch.length ? (
            <HBars rows={c.by_branch.map((b) => ({ label: b.branch, value: b.students }))} alt onSelect={setBranch} selected={branch} />
          ) : (
            <p className="muted">Every unplaced student has at least one eligible upcoming drive.</p>
          )}
        </VizCard>
        <VizCard title="Placement by branch" subtitle="Click a branch to filter the early-warning list." rows={s.by_branch} filename="placement_by_branch"
          columns={[
            { key: "branch", header: "Branch", value: (r) => r.branch },
            { key: "students", header: "Students", value: (r) => r.students, align: "right" },
            { key: "placed", header: "Placed", value: (r) => r.placed, align: "right" },
            { key: "pct", header: "Placed %", value: (r) => (r.students ? Math.round((100 * r.placed) / r.students) : 0), align: "right" },
          ]}>
          <HBars rows={s.by_branch.map((b) => ({ label: b.branch, value: b.placed, note: `${b.placed}/${b.students}` }))}
            max={Math.max(1, ...s.by_branch.map((b) => b.students))} onSelect={setBranch} selected={branch} />
        </VizCard>
        {forecast.data && <ForecastCard f={forecast.data} branch={branch} onBranch={setBranch} />}
        <Card title="Early warning" subtitle="Unplaced students likely to stay unplaced, with the reasons and a suggested intervention. Visible to the placement cell only." className="span-2"
          action={branch ? <span className="filter-pill">Branch: {branch}<button className="icon-btn" onClick={() => setBranch(null)} aria-label="Clear branch filter"><X size={12} /></button></span> : undefined}>
          {risk.data?.length ? (
            <DataTable rows={risk.data} columns={WARN_COLUMNS} rowKey={(r) => r.id} exportName="early_warning" pageSize={10}
              initialSort={{ key: "chance", dir: "asc" }} searchPlaceholder="Search name, roll no or reason"
              externalFilter={{ branch: branch ?? "" }} />
          ) : (
            <p className="muted">No unplaced student shows a warning pattern right now.</p>
          )}
        </Card>
        <RetrospectiveCard />
        {c && c.students.length > 0 && (
          <Card title={`Uncovered students (${plural(c.students.length, "shown")})`} className="span-2">
            <div className="row" style={{ gap: 6 }}>
              {c.students.map((st) => <span className="chip" key={st.id}>{st.name} · {st.branch} · {st.cgpa.toFixed(1)}{st.backlogs ? ` · ${st.backlogs} backlog` : ""}</span>)}
            </div>
          </Card>
        )}
      </div>
    </>
  );
}

type PipeSort = "ctc" | "applied" | "fill";

function SeasonCharts({ a }: { a: SeasonAnalytics }) {
  const [sort, setSort] = useState<PipeSort>("ctc");
  const [tier, setTier] = useState<string | null>(null);
  const top = Math.max(1, ...a.drives.map((d) => d.eligible));
  const fill = (d: SeasonAnalytics["drives"][number]) => (d.eligible ? d.applied / d.eligible : 0);
  const drives = a.drives.filter((d) => !tier || d.tier === tier).sort((x, y) =>
    sort === "ctc" ? y.ctc_lpa - x.ctc_lpa : sort === "applied" ? y.applied - x.applied : fill(y) - fill(x));
  return (
    <div className="grid grid-2" style={{ marginBottom: 16 }}>
      <VizCard title="Accepted offers over the season" subtitle="Week by week: running total and offers accepted that week. Click the legend to hide a series."
        className="span-2" rows={a.offers_by_week} filename="offers_by_week"
        columns={[{ key: "week", header: "Week", value: (r) => r.week }, { key: "accepted", header: "Accepted that week", value: (r) => r.accepted, align: "right" },
          { key: "cumulative", header: "Running total", value: (r) => r.cumulative, align: "right" }]}>
        <LineChart series={[
          { name: "Running total", points: a.offers_by_week.map((w) => ({ label: w.week, value: w.cumulative })) },
          { name: "Accepted that week", points: a.offers_by_week.map((w) => ({ label: w.week, value: w.accepted })) },
        ]} />
      </VizCard>
      <VizCard title="CTC distribution" subtitle="Accepted offers by package band." rows={a.ctc_hist} filename="ctc_distribution"
        columns={[{ key: "band", header: "Band", value: (r) => r.band }, { key: "offers", header: "Offers", value: (r) => r.offers, align: "right" }]}>
        <BarChart data={a.ctc_hist.map((b) => ({ label: b.band.replace(" LPA", ""), value: b.offers, hint: b.band }))} />
      </VizCard>
      <VizCard title="Placed students by tier" subtitle="Highest accepted offer per student. Click a tier to filter the drive pipeline." rows={a.by_tier}
        filename="placed_by_tier" columns={[{ key: "tier", header: "Tier", value: (r) => r.tier }, { key: "students", header: "Students", value: (r) => r.students, align: "right" }]}>
        <BarChart data={a.by_tier.map((t) => ({ label: t.tier, value: t.students, color: tierColor(t.tier) }))}
          onSelect={(d) => setTier((t) => (t === d.label ? null : d.label))} selected={tier} />
      </VizCard>
      <VizCard title="Top recruiters" subtitle="Accepted offers this season." rows={a.top_recruiters} filename="top_recruiters"
        columns={[{ key: "company", header: "Company", value: (r) => r.company }, { key: "offers", header: "Offers", value: (r) => r.offers, align: "right" }]}>
        <HBars rows={a.top_recruiters.map((r) => ({ label: r.company, value: r.offers }))} />
      </VizCard>
      <VizCard title="Drive pipeline" subtitle={`Eligible, applied and selected per drive${tier ? ` (${tier} only)` : ""}.`} rows={drives} filename="drive_pipeline"
        extra={
          <select value={sort} onChange={(e) => setSort(e.target.value as PipeSort)} aria-label="Sort drives" style={{ width: "auto", height: 30, fontSize: 13 }}>
            <option value="ctc">Highest CTC</option><option value="applied">Most applied</option><option value="fill">Best take-up</option>
          </select>
        }
        columns={[
          { key: "company", header: "Company", value: (r) => r.company }, { key: "role", header: "Role", value: (r) => r.role },
          { key: "ctc", header: "CTC (LPA)", value: (r) => r.ctc_lpa, align: "right" }, { key: "tier", header: "Tier", value: (r) => r.tier },
          { key: "status", header: "Status", value: (r) => r.status }, { key: "eligible", header: "Eligible", value: (r) => r.eligible, align: "right" },
          { key: "applied", header: "Applied", value: (r) => r.applied, align: "right" }, { key: "selected", header: "Selected", value: (r) => r.selected, align: "right" },
        ]}>
        <div className="stack" style={{ gap: 8, maxHeight: 360, overflow: "auto" }}>
          {tier && <span className="filter-pill" style={{ alignSelf: "flex-start" }}>{tier}<button className="icon-btn" onClick={() => setTier(null)} aria-label="Clear tier filter"><X size={12} /></button></span>}
          {drives.map((d) => (
            <Link key={d.id} to={`/cell/drives/${d.id}`} className="pipe-row"
              title={`${d.eligible} eligible · ${d.applied} applied (${Math.round(100 * fill(d))}%) · ${d.selected} selected`}>
              <span className="pipe-name"><strong>{d.company}</strong><em>{d.ctc_lpa} LPA · {d.status}</em></span>
              <span className="pipe-track">
                <i style={{ width: `${(100 * d.eligible) / top}%`, background: "var(--line)" }} />
                <i style={{ width: `${(100 * d.applied) / top}%`, background: tierColor(d.tier), opacity: 0.55 }} />
                <i style={{ width: `${(100 * d.selected) / top}%`, background: tierColor(d.tier) }} />
              </span>
              <span className="num small">{d.applied}/{d.eligible}</span>
            </Link>
          ))}
          {!drives.length && <p className="muted small">No drives in this tier.</p>}
        </div>
      </VizCard>
    </div>
  );
}

/** FUT-1: expected placements per branch by season end, next to where each branch stands today. */
function ForecastCard({ f, branch, onBranch }: { f: PlacementForecast; branch: string | null; onBranch: (b: string | null) => void }) {
  return (
    <VizCard title="Placement forecast" subtitle={`Expected placed by season end, from ${f.basis.upcoming_drives} upcoming drives and past selection rates. Click a branch to filter the early-warning list.`}
      className="span-2" rows={f.branches} filename="placement_forecast"
      columns={[
        { key: "branch", header: "Branch", value: (r) => r.branch },
        { key: "students", header: "Students", value: (r) => r.students, align: "right" },
        { key: "placed", header: "Placed today", value: (r) => r.placed, align: "right" },
        { key: "placed_pct", header: "Placed %", value: (r) => r.placed_pct, align: "right" },
        { key: "forecast_placed", header: "Forecast placed", value: (r) => r.forecast_placed, align: "right" },
        { key: "forecast_pct", header: "Forecast %", value: (r) => r.forecast_pct, align: "right" },
        { key: "gain", header: "Expected gain", value: (r) => r.forecast_placed - r.placed, align: "right" },
      ]}>
      <div className="grid grid-2">
        <div>
          <h3 style={{ marginBottom: 6 }}>Placed today</h3>
          <HBars max={100} onSelect={onBranch} selected={branch}
            rows={f.branches.map((b) => ({ label: b.branch, value: b.placed_pct, note: `${b.placed}/${b.students}` }))} />
        </div>
        <div>
          <h3 style={{ marginBottom: 6 }}>Forecast by season end</h3>
          <HBars max={100} onSelect={onBranch} selected={branch}
            rows={f.branches.map((b) => ({ label: b.branch, value: b.forecast_pct, note: `${b.forecast_pct}% · ~${b.forecast_placed}` }))} />
        </div>
      </div>
      <p className="small faint" style={{ marginTop: 10 }}>
        {f.basis.method} Tier rates used: {Object.entries(f.basis.tier_rates).map(([t, r]) => `${t} ${Math.round(r * 100)}%`).join(", ") || "none yet"}; default {Math.round(f.basis.default_rate * 100)}%.
      </p>
    </VizCard>
  );
}

/** FUT-5/6: the end-of-season report, viewable here and exportable as PDF and CSV. */
function RetrospectiveCard() {
  const [season, setSeason] = useState<string>("");
  const q = useQuery({
    queryKey: ["cell", "retrospective", season],
    queryFn: () => api.get<Retrospective>(`/api/cell/reports/retrospective${season ? `?season=${encodeURIComponent(season)}` : ""}`),
  });
  const r = q.data;
  const qs = r ? `?season=${encodeURIComponent(r.season)}` : "";
  return (
    <Card title="Season retrospective" subtitle="End-of-season report for the institute and its rankings submissions."
      className="span-2"
      action={r && (
        <div className="row">
          {r.available_seasons.length > 1 && (
            <select value={r.season} onChange={(e) => setSeason(e.target.value)} aria-label="Season" style={{ width: 120 }}>
              {r.available_seasons.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          )}
          <Button size="small" onClick={() => void download(`/api/cell/reports/retrospective.pdf${qs}`, `retrospective_${r.season}.pdf`)}>Download PDF</Button>
          <Button size="small" onClick={() => void download(`/api/cell/reports/retrospective.csv${qs}`, `retrospective_${r.season}.csv`)}>CSV</Button>
        </div>
      )}>
      {q.isLoading ? <Loading /> : !r ? <ErrorBox error={q.error} /> : (
        <div className="stack">
          <div className="kpi-grid">
            <div className="kpi-tile accent"><div className="v">{r.summary.placed_pct}%</div><div className="l">placed ({r.summary.placed} of {r.summary.students}), season {r.season}</div></div>
            <div className="kpi-tile"><div className="v">{r.summary.offers_accepted}</div><div className="l">offers accepted</div></div>
            <div className="kpi-tile"><div className="v">{r.summary.median_ctc ?? "—"}</div><div className="l">median CTC (LPA)</div></div>
            <div className="kpi-tile"><div className="v">{r.summary.companies}</div><div className="l">companies, {r.summary.drives} drives</div></div>
            <div className="kpi-tile"><div className="v">{r.drive_day.avg_delay_min ?? "—"}</div><div className="l">avg interview start delay (min)</div></div>
            <div className="kpi-tile"><div className="v">{r.drive_day.replans_applied}</div><div className="l">re-plans applied, {r.drive_day.no_shows} no-shows</div></div>
          </div>
          <div className="grid grid-2">
            <div>
              <h3 style={{ marginBottom: 6 }}>Offers by tier</h3>
              <BarChart data={r.by_tier.map((t) => ({ label: t.tier, value: t.offers, color: tierColor(t.tier) }))} />
            </div>
            <div>
              <h3 style={{ marginBottom: 6 }}>Placed by branch</h3>
              <HBars max={100} rows={r.by_branch.map((b) => ({ label: b.branch, value: b.placed_pct, note: `${b.placed}/${b.students} · median ${b.median_ctc ?? "—"}` }))} />
            </div>
          </div>
          {r.trends.length > 1 && (
            <div>
              <h3 style={{ marginBottom: 6 }}>Offers across seasons</h3>
              <LineChart points={r.trends.map((t) => ({ label: t.season, value: t.offers }))} />
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
