import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api, download } from "../../api/client";
import type { Application, Offer, PoolPreview, RecruiterAnalytics, RecruiterOverview, Slot } from "../../api/types";
import { Funnel, HBars } from "../../components/charts";
import { Badge, Button, Card, ConfirmButton, Empty, ErrorBox, Field, Loading, Tabs, useAction, useToast } from "../../components/ui";
import { APPLICANT_COLUMNS, StageBars } from "../../components/applicants";
import { DataTable } from "../../components/DataTable";
import { PanelLinkList } from "../../components/PanelLinks";
import { ScheduleBoard } from "../../components/ScheduleBoard";
import { BarChart, LineChart } from "../../components/viz";
import { clock, lpa, plural, ruleText } from "../../lib/format";
import { useLive } from "../../lib/live";

const BRANCHES = ["CSE", "ECE", "EEE", "ME", "CE", "CHE", "PE"];

function WhatIf({ token, ov }: { token: string; ov: RecruiterOverview }) {
  const current = (field: string) => ov.rules.find((r) => r.field === field && r.approved);
  const [cgpa, setCgpa] = useState<number>(Number(current("cgpa")?.value ?? 6));
  const [backlogs, setBacklogs] = useState<number>(Number(current("backlogs")?.value ?? 2));
  const [branches, setBranches] = useState<string[]>((current("branch")?.value as string[] | undefined) ?? []);
  const [result, setResult] = useState<PoolPreview | null>(null);
  const toast = useToast();
  const body = useMemo(() => ({ cgpa_min: cgpa, max_backlogs: backlogs, branches }), [cgpa, backlogs, branches]);
  useEffect(() => {
    const t = window.setTimeout(() => {
      api.post<PoolPreview>("/api/r/pool-preview", body, token).then(setResult).catch(() => undefined);
    }, 200);
    return () => window.clearTimeout(t);
  }, [body, token]);
  const request = useAction(() => api.post("/api/r/criteria-request", body, token), {
    success: "Sent to the placement cell for approval.",
  });
  const toggle = (b: string) => setBranches((xs) => (xs.includes(b) ? xs.filter((x) => x !== b) : [...xs, b]));
  return (
    <Card title="Is the pool right?" subtitle="Try criteria before committing. You see counts only; personal details appear once students apply.">
      <div className="grid grid-2">
        <div className="stack">
          <Field label={`Minimum CGPA: ${cgpa.toFixed(1)}`}>
            <input type="range" min={5} max={9.5} step={0.1} value={cgpa} onChange={(e) => setCgpa(Number(e.target.value))} />
          </Field>
          <Field label={`Max active backlogs: ${backlogs}`}>
            <input type="range" min={0} max={5} step={1} value={backlogs} onChange={(e) => setBacklogs(Number(e.target.value))} />
          </Field>
          <div>
            <div className="small muted" style={{ marginBottom: 6 }}>Branches (none selected = all)</div>
            <div className="row" style={{ gap: 6 }}>
              {BRANCHES.map((b) => (
                <button key={b} className={`chip ${branches.includes(b) ? "" : "missing"}`} style={{ cursor: "pointer" }}
                  aria-pressed={branches.includes(b)} onClick={() => toggle(b)}>{b}</button>
              ))}
            </div>
          </div>
        </div>
        <div className="stack">
          {result ? (
            <>
              <div className="row" style={{ gap: 28 }}>
                <div className="stat"><div className="value">{result.current}</div><div className="label">eligible today</div></div>
                <div className="stat"><div className="value" style={{ color: "var(--signal)" }}>{result.what_if}</div><div className="label">with these criteria</div></div>
              </div>
              {Object.values(result.skill_match).some(Boolean) && (
                <div>
                  <div className="small muted" style={{ marginBottom: 6 }}>Skill match with your JD</div>
                  <HBars rows={Object.entries(result.skill_match).map(([label, value]) => ({ label, value }))} />
                </div>
              )}
              <Button disabled={result.what_if === result.current} busy={request.isPending}
                onClick={() => request.mutate(undefined, { onError: () => toast("Couldn't send the request.", true) })}>
                Ask the cell to apply these criteria
              </Button>
            </>
          ) : <Loading />}
        </div>
      </div>
    </Card>
  );
}

function Applicants({ token, ov }: { token: string; ov: RecruiterOverview }) {
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [ctc, setCtc] = useState(ov.drive.ctc_lpa);
  const fileRef = useRef<HTMLInputElement>(null);
  const [resultsRound, setResultsRound] = useState(ov.rounds.find((r) => r.kind === "test")?.id ?? 0);
  const list = useQuery({ queryKey: ["r", "applicants", token], queryFn: () => api.get<Application[]>("/api/r/applicants", token) });
  const stage = useAction(
    (p: { action: string; round_id?: number }) => api.post<{ changed: number }>("/api/r/applicants/stage", { application_ids: [...selected], ...p }, token),
    { success: (r) => `Updated ${plural(r.changed, "candidate")}.`, invalidate: [["r"]], onDone: () => setSelected(new Set()) },
  );
  const results = useAction(
    async (file: File) => api.post<{ passed: number; failed: number; unmatched: string[] }>("/api/r/results", { round_id: resultsRound, csv_text: await file.text() }, token),
    { success: (r) => `Recorded ${r.passed} pass, ${r.failed} fail${r.unmatched.length ? `; ${r.unmatched.length} rows didn't match` : ""}.`, invalidate: [["r"]] },
  );
  const offer = useAction(
    (application_id: number) => api.post<Offer>("/api/r/offers", { application_id, ctc_lpa: ctc }, token),
    { success: "Offer released. The student has been notified.", invalidate: [["r"]] },
  );
  const [stageFilter, setStageFilter] = useState<string | null>(null);
  const tests = ov.rounds.filter((r) => r.kind === "test");
  const apps = list.data ?? [];
  return (
    <Card title={`Applicants (${apps.length})`} subtitle="Shortlist here, or upload results from your ATS as CSV (roll_no, result)."
      action={
        <div className="row">
          <Button size="small" onClick={() => void download("/api/r/applicants.csv", `${ov.drive.company}_candidates.csv`, token)}>Export CSV</Button>
          <Button size="small" onClick={() => void download("/api/r/resumes.zip", `${ov.drive.company}_resumes.zip`, token)}>Resume bundle</Button>
        </div>
      }>
      <div className="row" style={{ marginBottom: 12 }}>
        <Button size="small" variant="primary" disabled={!selected.size} onClick={() => stage.mutate({ action: "shortlist" })}>Shortlist {selected.size || ""}</Button>
        {tests.map((t) => (
          <Button key={t.id} size="small" disabled={!selected.size} onClick={() => stage.mutate({ action: "pass_round", round_id: t.id })}>Passed {t.name}</Button>
        ))}
        <ConfirmButton size="small" disabled={!selected.size} title={`Reject ${selected.size} candidate${selected.size === 1 ? "" : "s"}?`}
          confirmLabel="Reject" message="Candidates are notified and their remaining interview slots are cancelled."
          onConfirm={() => stage.mutate({ action: "reject" })}>Reject</ConfirmButton>
        <span className="spacer" />
        {tests.length > 0 && (
          <>
            <select value={resultsRound} onChange={(e) => setResultsRound(Number(e.target.value))} style={{ width: 200 }} aria-label="Results round">
              {tests.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
            <input ref={fileRef} type="file" accept=".csv" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) results.mutate(f); e.target.value = ""; }} />
            <Button size="small" busy={results.isPending} onClick={() => fileRef.current?.click()}>Upload results CSV</Button>
          </>
        )}
      </div>
      {list.isLoading ? <Loading /> : apps.length === 0 ? <Empty title="No applications yet">Eligible students are applying now.</Empty> : (
        <>
          <StageBars apps={apps} selected={stageFilter} onSelect={setStageFilter} />
          <DataTable rows={apps} rowKey={(a) => a.id} selection={{ selected, onChange: setSelected, id: (a) => a.id }}
            initialSort={{ key: "cgpa", dir: "desc" }} searchPlaceholder="Search candidate, roll no or branch"
            externalFilter={{ stage: stageFilter ?? "" }} pageSize={25}
            columns={[
              ...APPLICANT_COLUMNS.filter((c) => c.key !== "stage"),
              { key: "stage", header: "Stage", value: (a) => a.stage, filter: true,
                render: (a) => <Badge tone={a.status === "rejected" ? "bad" : a.status === "selected" ? "ok" : "info"}>{a.offer_status ? `offer ${a.offer_status}` : a.stage}</Badge> },
              { key: "actions", header: "", sortable: false, pinned: true, csv: false,
                render: (a) => (
                  <span className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
                    <Button size="small" variant="ghost" onClick={() => void download(`/api/r/resumes/${a.id}`, `${a.roll_no}.pdf`, token)}>Resume</Button>
                    {a.status === "selected" && !a.offer_status && (
                      <Button size="small" variant="primary" busy={offer.isPending} onClick={() => offer.mutate(a.id)}>Release offer</Button>
                    )}
                  </span>
                ) },
            ]} />
        </>
      )}
      {apps.some((a) => a.status === "selected" && !a.offer_status) && (
        <div className="row" style={{ marginTop: 12 }}>
          <Field label="Offer CTC (LPA)"><input type="number" step={0.5} value={ctc} onChange={(e) => setCtc(Number(e.target.value))} style={{ width: 120 }} /></Field>
        </div>
      )}
    </Card>
  );
}

function PanelLinks({ token }: { token: string }) {
  const links = useQuery({ queryKey: ["r", "panel-links", token], queryFn: () => api.get<{ panel_id: number; name: string; round: string; room: string; url: string }[]>("/api/r/panel-links", token) });
  if (!links.data?.length) return null;
  return (
    <Card title="Links for your interviewers" subtitle="Each opens a one-screen panel view: next candidate, resume, and Done.">
      <PanelLinkList links={links.data} heading="Interview panel links" />
    </Card>
  );
}

function AnalyticsTab({ token }: { token: string }) {
  const q = useQuery({ queryKey: ["r", "analytics", token], queryFn: () => api.get<RecruiterAnalytics>("/api/r/analytics", token) });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error} />;
  const a = q.data;
  const t = a.totals;
  return (
    <>
      <div className="kpi-grid">
        <div className="kpi-tile accent"><div className="v">{t.applicants}</div><div className="l">applicants</div></div>
        <div className="kpi-tile"><div className="v">{t.shortlisted}</div><div className="l">shortlisted</div></div>
        <div className="kpi-tile"><div className="v">{t.selected}</div><div className="l">selected</div></div>
        <div className="kpi-tile"><div className="v">{t.offers_accepted}</div><div className="l">offers accepted</div></div>
        <div className="kpi-tile"><div className="v">{t.avg_cgpa?.toFixed(2) ?? "—"}</div><div className="l">average CGPA</div></div>
        <div className="kpi-tile"><div className="v">{t.median_skill_match === null ? "—" : `${t.median_skill_match}%`}</div><div className="l">median JD skill match</div></div>
      </div>
      <div className="grid grid-2">
        <Card title="Applications per day" subtitle="When eligible students applied." className="span-2">
          {a.daily.length ? <LineChart points={a.daily.map((d) => ({ label: d.date.slice(5), value: d.applications }))} />
            : <p className="muted">No applications yet.</p>}
        </Card>
        <Card title="Applicant CGPA" subtitle="How strong the pool is, in half-point buckets.">
          <BarChart data={a.cgpa_histogram.map((b) => ({ label: b.bucket, value: b.count, hint: `${b.count} applicants with CGPA ${b.bucket}` }))} />
        </Card>
        <Card title="By branch" subtitle="Applicants and how far they've got.">
          <HBars rows={a.branches.map((b) => ({ label: b.branch, value: b.applicants, note: `${b.applicants} applied · ${b.shortlisted} shortlisted · ${b.selected} selected` }))} />
        </Card>
        <Card title="JD skill coverage" subtitle="Share of applicants whose profile lists each skill your JD asks for.">
          {a.skill_coverage.length ? (
            <HBars max={100} rows={a.skill_coverage.map((s) => ({ label: s.skill, value: s.pct, note: `${s.pct}%` }))} />
          ) : <p className="muted">The JD doesn't list specific skills.</p>}
        </Card>
        <Card title="Pipeline" subtitle="Candidates at each stage.">
          <BarChart data={a.funnel.map((f) => ({ label: f.stage, value: f.count, color: "var(--signal)" }))} />
        </Card>
        {a.panels.length > 0 && (
          <Card title="Interview panels" subtitle="Outcomes and pace against the planned slot length." className="span-2">
            <HBars max={100} rows={a.panels.map((p) => {
              const n = p.pass + p.fail;
              const rate = n ? Math.round((100 * p.pass) / n) : 0;
              return { label: p.panel.split(" (")[0], value: rate, note: n ? `${rate}% cleared of ${n}` : "no results yet" };
            })} />
            <div className="table-wrap" style={{ marginTop: 10 }}>
              <table>
                <thead><tr><th>Panel</th><th>Cleared</th><th>Not cleared</th><th>Avg minutes</th><th>Planned</th></tr></thead>
                <tbody>{a.panels.map((p) => (
                  <tr key={p.panel}><td>{p.panel}</td><td className="num">{p.pass}</td><td className="num">{p.fail}</td>
                    <td className="num" style={{ color: p.avg_min && p.avg_min > p.planned_min + 2 ? "var(--red)" : undefined }}>{p.avg_min ?? "—"}</td>
                    <td className="num">{p.planned_min}</td></tr>
                ))}</tbody>
              </table>
            </div>
          </Card>
        )}
      </div>
    </>
  );
}

function ScheduleTabR({ token }: { token: string }) {
  const q = useQuery({ queryKey: ["r", "schedule", token], queryFn: () => api.get<Slot[]>("/api/r/schedule", token), refetchInterval: 30_000 });
  return (
    <Card title="Interview timeline" subtitle="Live projection per panel: bars move as interviews run long or short. The red line is now.">
      {q.isLoading ? <Loading /> : <ScheduleBoard slots={q.data ?? []} />}
    </Card>
  );
}

type RTab = "overview" | "candidates" | "analytics" | "schedule" | "whatif";

export function RecruiterDrive() {
  const token = useParams().token ?? "";
  const live = useLive(Boolean(token), token);
  const [tab, setTab] = useState<RTab>("overview");
  const ov = useQuery({ queryKey: ["r", "overview", token], queryFn: () => api.get<RecruiterOverview>("/api/r/overview", token), retry: false });
  if (ov.isLoading) return <div className="main"><Loading /></div>;
  if (ov.error || !ov.data) return <div className="main"><ErrorBox error={ov.error} /></div>;
  const o = ov.data;
  const inProcess = o.target.remaining_candidates;
  return (
    <main className="main recruiter" style={{ margin: "0 auto", maxWidth: 1240 }}>
      <section className="hero">
        <div>
          <div className="small" style={{ color: "#9fb3cc", fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase" }}>Recruiting team · {o.drive.tier}</div>
          <h1>{o.drive.company}: {o.drive.role}</h1>
          <p>{lpa(o.drive.ctc_lpa)}. Secure drive link from the placement cell; no account needed.{" "}
            <span className={`live-dot ${live === "live" ? "live" : ""}`}>{live === "live" ? "Live" : "Connecting"}</span></p>
        </div>
        <div className="kpis">
          <div className="kpi"><div className="v">{o.funnel[0]?.count ?? 0}</div><div className="l">applied</div></div>
          <div className="kpi"><div className="v">{inProcess}</div><div className="l">in process</div></div>
          <div className="kpi"><div className="v">{o.offers.accepted}/{o.target.hires}</div><div className="l">hires vs target</div></div>
          <div className="kpi"><div className="v">{o.pending.filter((p) => p.mine).length}</div><div className="l">waiting on you</div></div>
        </div>
      </section>
      <Tabs<RTab> value={tab} onChange={setTab} tabs={[
        { id: "overview", label: "Overview" },
        { id: "candidates", label: "Candidates" },
        { id: "analytics", label: "Analytics" },
        { id: "schedule", label: "Schedule" },
        { id: "whatif", label: "Criteria what-if" },
      ]} />
      {tab === "overview" && (
        <div className="grid grid-2">
          <Card title="Your next steps">
            {o.pending.length === 0 ? <p className="muted">Nothing waiting on you right now.</p> : o.pending.map((p, i) => (
              <div className="action" key={i} style={{ gridTemplateColumns: "1fr auto" }}>
                <div><strong>{p.title}</strong><div className="small muted">{p.detail}</div></div>
                <Badge tone={p.mine ? "warn" : undefined}>{p.mine ? "You" : "Placement cell"}</Badge>
              </div>
            ))}
          </Card>
          <Card title="Will this drive get you your hires?"
            subtitle={o.target.on_track ? `${o.target.remaining_candidates} candidates still in process for ${o.target.hires} hires.` : `Only ${o.target.remaining_candidates} candidates left for ${o.target.hires} hires. Consider widening criteria.`}>
            <Funnel stages={o.funnel} target={o.target.hires} />
            <p className="small muted" style={{ marginTop: 8 }}>
              Offers: {o.offers.accepted} accepted, {o.offers.pending} pending, {o.offers.declined} declined.
            </p>
          </Card>
          {o.panels.length > 0 && (
            <Card title="Drive day progress" className="span-2">
              <div className="board-grid">
                {o.panels.map((p) => (
                  <div className="board" key={p.panel_id}>
                    <h3><span>{p.name} · {p.room}</span><span>{p.round}</span></h3>
                    <div className="row between" style={{ marginTop: 8 }}>
                      <div><div className="label">Finishes about</div><div className="big">{p.finish ? clock(p.finish) : "—"}</div></div>
                      <div style={{ textAlign: "right" }}>
                        <div className="label">{p.done} done · {p.remaining} left</div>
                        <div className="label">avg {p.avg_min ?? "—"} min (planned {p.planned_min})</div>
                        {p.late_min > 2 && <div style={{ color: "#ff9a8a" }}>{p.late_min} min behind</div>}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          )}
          <PanelLinks token={token} />
          <Card title="Criteria and process">
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {o.rules.map((r, i) => (
                <li key={i}>{ruleText(r.field, r.op, r.value)} {!r.approved && <Badge tone="warn">pending approval</Badge>}</li>
              ))}
            </ul>
            <ol style={{ marginTop: 12, paddingLeft: 18 }}>
              {o.rounds.map((r) => <li key={r.id}>{r.name} <span className="small faint">({plural(r.panels.length, "panel")})</span></li>)}
            </ol>
            {o.drive.skills.length > 0 && (
              <div className="row" style={{ gap: 6, marginTop: 10 }}>{o.drive.skills.map((s) => <span key={s} className="chip">{s}</span>)}</div>
            )}
          </Card>
        </div>
      )}
      {tab === "candidates" && <Applicants token={token} ov={o} />}
      {tab === "analytics" && <AnalyticsTab token={token} />}
      {tab === "schedule" && <ScheduleTabR token={token} />}
      {tab === "whatif" && <WhatIf token={token} ov={o} />}
    </main>
  );
}
