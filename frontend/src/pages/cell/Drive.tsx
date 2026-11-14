import { useQuery } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, download } from "../../api/client";
import type { Application, Drive, DriveStatus, FairnessAudit, Offer, Round, Rule, Slot } from "../../api/types";
import { HBars } from "../../components/charts";
import { BarChart } from "../../components/viz";
import { APPLICANT_COLUMNS, StageBars } from "../../components/applicants";
import { DataTable } from "../../components/DataTable";
import { PanelLinkList } from "../../components/PanelLinks";
import { ScheduleBoard } from "../../components/ScheduleBoard";
import { useCopyLink } from "../../lib/clipboard";
import { Badge, Button, Card, ConfirmButton, Empty, ErrorBox, Field, Loading, PageHead, Tabs, useAction } from "../../components/ui";
import { clock, dateShort, fromLocalInput, lpa, plural, ruleText, toLocalInput } from "../../lib/format";

interface DriveDetail {
  drive: Drive;
  rules: Rule[];
  rounds: Round[];
}

type Tab = "rules" | "fairness" | "rounds" | "applicants" | "schedule" | "links";

const FIELDS: { id: Rule["field"]; label: string; op: Rule["op"] }[] = [
  { id: "cgpa", label: "Minimum CGPA", op: ">=" },
  { id: "backlogs", label: "Max active backlogs", op: "<=" },
  { id: "branch", label: "Branches", op: "in" },
  { id: "programme", label: "Programmes", op: "in" },
  { id: "grad_year", label: "Graduation years", op: "in" },
];

function parseValue(field: Rule["field"], raw: string): Rule["value"] {
  if (field === "cgpa" || field === "backlogs") return Number(raw);
  const parts = raw.split(",").map((s) => s.trim()).filter(Boolean);
  return field === "grad_year" ? parts.map(Number) : parts.map((s) => s.toUpperCase());
}

function RuleRow({ driveId, rule }: { driveId: number; rule: Rule }) {
  const [editing, setEditing] = useState(false);
  const [raw, setRaw] = useState(Array.isArray(rule.value) ? rule.value.join(", ") : String(rule.value));
  const save = useAction(
    (approved: boolean) =>
      api.put(`/api/cell/drives/${driveId}/rules/${rule.id}`, {
        field: rule.field, op: rule.op, value: editing ? parseValue(rule.field, raw) : rule.value, approved,
      }),
    { success: "Rule saved. Eligibility recomputed.", invalidate: [["cell"]], onDone: () => setEditing(false) },
  );
  const remove = useAction(() => api.del(`/api/cell/drives/${driveId}/rules/${rule.id}`), { success: "Rule removed.", invalidate: [["cell"]] });
  const conf = Math.round(rule.confidence * 100);
  return (
    <tr>
      <td>
        {editing ? (
          <input value={raw} onChange={(e) => setRaw(e.target.value)} aria-label="Rule value" />
        ) : (
          <strong>{ruleText(rule.field, rule.op, rule.value)}</strong>
        )}
        {rule.source_text && <div className="small faint">“{rule.source_text}”</div>}
      </td>
      <td>
        <Badge tone={conf < 75 ? "warn" : "ok"}>{conf}% sure</Badge>
      </td>
      <td>{rule.approved ? <Badge tone="ok">Approved</Badge> : <Badge tone="warn">Waiting</Badge>}</td>
      <td>
        <div className="row" style={{ gap: 6 }}>
          {(!rule.approved || editing) && (
            <Button size="small" variant="primary" busy={save.isPending} onClick={() => save.mutate(true)}>Approve</Button>
          )}
          <Button size="small" onClick={() => setEditing((e) => !e)}>{editing ? "Cancel" : "Edit"}</Button>
          <ConfirmButton size="small" title="Remove this rule?" confirmLabel="Remove rule"
            message="Eligibility for this drive is recomputed without this rule." onConfirm={() => remove.mutate(undefined)}>Remove</ConfirmButton>
        </div>
      </td>
    </tr>
  );
}

function RulesTab({ data }: { data: DriveDetail }) {
  const d = data.drive;
  const [jd, setJd] = useState(d.jd_text);
  const [newField, setNewField] = useState<Rule["field"]>("cgpa");
  const [newValue, setNewValue] = useState("");
  const extract = useAction(() => api.post<Rule[]>(`/api/cell/drives/${d.id}/extract`, { jd_text: jd, replace_existing: true }), {
    success: (rules) => `Extracted ${plural(rules.length, "rule")}. Review and approve them.`,
    invalidate: [["cell"]],
  });
  const approveAll = useAction(() => api.post(`/api/cell/drives/${d.id}/rules/approve-all`), { success: "All rules approved.", invalidate: [["cell"]] });
  const add = useAction(
    () => api.post(`/api/cell/drives/${d.id}/rules`, {
      field: newField, op: FIELDS.find((f) => f.id === newField)?.op, value: parseValue(newField, newValue), approved: true,
    }),
    { success: "Rule added.", invalidate: [["cell"]], onDone: () => setNewValue("") },
  );
  const usedFields = new Set(data.rules.map((r) => r.field));
  return (
    <div className="grid grid-2">
      <Card title="Eligibility rules" subtitle={`${d.eligible ?? 0} students eligible with the approved rules.`}
        action={data.rules.some((r) => !r.approved) ? <Button size="small" variant="primary" busy={approveAll.isPending} onClick={() => approveAll.mutate(undefined)}>Approve all</Button> : undefined}
        className="span-2">
        {data.rules.length === 0 ? (
          <Empty title="No rules yet">Extract them from the JD, or add one below. Without rules, every student is eligible.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Rule and evidence</th><th>Confidence</th><th>Status</th><th /></tr></thead>
              <tbody>{data.rules.map((r) => <RuleRow key={`${r.id}-${String(r.value)}-${r.approved}`} driveId={d.id} rule={r} />)}</tbody>
            </table>
          </div>
        )}
        <hr className="divider" />
        <div className="row">
          <select value={newField} onChange={(e) => setNewField(e.target.value as Rule["field"])} style={{ maxWidth: 220 }} aria-label="Rule field">
            {FIELDS.filter((f) => !usedFields.has(f.id)).map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
          </select>
          <input value={newValue} onChange={(e) => setNewValue(e.target.value)} placeholder="e.g. 7.5 or CSE, ECE" style={{ maxWidth: 240 }} aria-label="Rule value" />
          <Button busy={add.isPending} disabled={!newValue || usedFields.has(newField)} onClick={() => add.mutate(undefined)}>Add rule</Button>
        </div>
      </Card>
      <Card title="Job description" subtitle="Edit and re-extract if the recruiter sends an updated JD." className="span-2">
        <textarea value={jd} onChange={(e) => setJd(e.target.value)} style={{ minHeight: 180 }} />
        <div className="row" style={{ marginTop: 10 }}>
          <Button busy={extract.isPending} disabled={jd.trim().length < 20} onClick={() => extract.mutate(undefined)}>Extract rules from JD</Button>
          <span className="small muted">Skills found: {d.skills.join(", ") || "none yet"}</span>
        </div>
      </Card>
    </div>
  );
}

function RoundsTab({ data }: { data: DriveDetail }) {
  const d = data.drive;
  const [round, setRound] = useState({ name: "", kind: "interview" as "test" | "interview", duration_min: 30 });
  const [panel, setPanel] = useState({ round_id: 0, name: "", room: "", interviewer: "" });
  const nextSeq = Math.max(0, ...data.rounds.map((r) => r.seq)) + 1;
  const addRound = useAction(() => api.post(`/api/cell/drives/${d.id}/rounds`, { ...round, seq: nextSeq }), {
    success: "Round added.", invalidate: [["cell"]], onDone: () => setRound({ ...round, name: "" }),
  });
  const delRound = useAction((id: number) => api.del(`/api/cell/drives/${d.id}/rounds/${id}`), { success: "Round removed.", invalidate: [["cell"]] });
  const addPanel = useAction(() => api.post(`/api/cell/drives/${d.id}/panels`, { ...panel, interviewer: panel.interviewer || null }), {
    success: "Panel added.", invalidate: [["cell"]], onDone: () => setPanel({ ...panel, name: "", room: "", interviewer: "" }),
  });
  const delPanel = useAction((id: number) => api.del(`/api/cell/drives/${d.id}/panels/${id}`), { success: "Panel removed.", invalidate: [["cell"]] });
  const interviews = data.rounds.filter((r) => r.kind === "interview");
  return (
    <div className="grid grid-2">
      <Card title="Rounds" subtitle="Tests are bulk results; interview rounds are scheduled into panel slots.">
        {data.rounds.map((r) => (
          <div key={r.id} className="action" style={{ gridTemplateColumns: "40px 1fr auto" }}>
            <span className="when">{r.seq}</span>
            <div>
              <strong>{r.name}</strong> <span className="small faint">{r.kind}, {r.duration_min} min</span>
              {r.kind === "interview" && (
                <div className="small muted">
                  {r.panels.length ? r.panels.map((p) => (
                    <span key={p.id} className="chip" style={{ marginRight: 6 }}>
                      {p.name}, {p.room}
                      <button className="btn ghost small" style={{ height: 18, padding: 0 }} aria-label={`Remove ${p.name}`} onClick={() => delPanel.mutate(p.id)}>×</button>
                    </span>
                  )) : "No panels yet"}
                </div>
              )}
            </div>
            <ConfirmButton size="small" title={`Remove ${r.name}?`} confirmLabel="Remove round"
              message="The round and its panels are deleted, and those panel links stop working." onConfirm={() => delRound.mutate(r.id)}>Remove</ConfirmButton>
          </div>
        ))}
        <hr className="divider" />
        <div className="form-grid">
          <Field label="Round name"><input value={round.name} onChange={(e) => setRound({ ...round, name: e.target.value })} placeholder="Technical interview" /></Field>
          <Field label="Kind">
            <select value={round.kind} onChange={(e) => setRound({ ...round, kind: e.target.value as "test" | "interview" })}>
              <option value="interview">Interview</option>
              <option value="test">Test</option>
            </select>
          </Field>
          <Field label="Minutes"><input type="number" min={5} value={round.duration_min} onChange={(e) => setRound({ ...round, duration_min: Number(e.target.value) })} /></Field>
        </div>
        <Button style={{ marginTop: 10 }} busy={addRound.isPending} disabled={round.name.length < 2} onClick={() => addRound.mutate(undefined)}>Add round {nextSeq}</Button>
      </Card>
      <Card title="Add a panel" subtitle="A panel is an interviewer (or group) in a room.">
        {interviews.length === 0 ? (
          <p className="muted">Add an interview round first.</p>
        ) : (
          <div className="stack">
            <Field label="Round">
              <select value={panel.round_id} onChange={(e) => setPanel({ ...panel, round_id: Number(e.target.value) })}>
                <option value={0}>Choose…</option>
                {interviews.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
            </Field>
            <div className="form-grid">
              <Field label="Panel name"><input value={panel.name} onChange={(e) => setPanel({ ...panel, name: e.target.value })} placeholder="Panel D" /></Field>
              <Field label="Room"><input value={panel.room} onChange={(e) => setPanel({ ...panel, room: e.target.value })} placeholder="LH 104" /></Field>
              <Field label="Interviewer"><input value={panel.interviewer} onChange={(e) => setPanel({ ...panel, interviewer: e.target.value })} /></Field>
            </div>
            <Button busy={addPanel.isPending} disabled={!panel.round_id || !panel.name || !panel.room} onClick={() => addPanel.mutate(undefined)}>Add panel</Button>
          </div>
        )}
      </Card>
    </div>
  );
}

function ApplicantsTab({ data }: { data: DriveDetail }) {
  const d = data.drive;
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [stageFilter, setStageFilter] = useState<string | null>(null);
  const apps = useQuery({ queryKey: ["cell", "apps", d.id], queryFn: () => api.get<Application[]>(`/api/cell/drives/${d.id}/applications`) });
  const stage = useAction(
    (p: { action: string; round_id?: number }) => api.post<{ changed: number }>(`/api/cell/drives/${d.id}/applications/stage`, { application_ids: [...selected], ...p }),
    { success: (r) => `Updated ${plural(r.changed, "candidate")}.`, invalidate: [["cell"]], onDone: () => setSelected(new Set()) },
  );
  if (apps.isLoading) return <Loading />;
  const list = apps.data ?? [];
  const tests = data.rounds.filter((r) => r.kind === "test");
  return (
    <div className="stack">
    {d.status === "open" && <NudgeCard driveId={d.id} />}
    <Card title={`${plural(list.length, "applicant")}`} subtitle="Recruiters usually do this from their link; the cell can step in."
      action={
        <div className="row">
          <Button size="small" disabled={!selected.size} onClick={() => stage.mutate({ action: "shortlist" })}>Shortlist</Button>
          {tests.map((t) => (
            <Button key={t.id} size="small" disabled={!selected.size} onClick={() => stage.mutate({ action: "pass_round", round_id: t.id })}>Passed {t.name}</Button>
          ))}
          <ConfirmButton size="small" disabled={!selected.size} title={`Reject ${selected.size} candidate${selected.size === 1 ? "" : "s"}?`}
            confirmLabel="Reject" message="They are told by notification, and their remaining interview slots are cancelled."
            onConfirm={() => stage.mutate({ action: "reject" })}>Reject</ConfirmButton>
        </div>
      }>
      {list.length === 0 ? <Empty title="No applications yet" /> : (
        <>
          <StageBars apps={list} selected={stageFilter} onSelect={setStageFilter} />
          <DataTable rows={list} columns={APPLICANT_COLUMNS} rowKey={(a) => a.id} exportName={`applicants_${d.company}_${d.id}`}
            selection={{ selected, onChange: setSelected, id: (a) => a.id }} initialSort={{ key: "cgpa", dir: "desc" }}
            searchPlaceholder="Search name, roll no or branch" externalFilter={{ stage: stageFilter ?? "" }} pageSize={25} />
        </>
      )}
    </Card>
    </div>
  );
}

/** Remind eligible students who have not applied. HireFlo also sends one automatically a day before the deadline. */
function NudgeCard({ driveId }: { driveId: number }) {
  const q = useQuery({ queryKey: ["cell", "nudge", driveId], queryFn: () => api.get<{ pending: number; open: boolean; last_sent_at: string | null; last_count: number | null }>(`/api/cell/drives/${driveId}/nudge`) });
  const send = useAction(() => api.post<{ sent: number }>(`/api/cell/drives/${driveId}/nudge`), {
    success: (r) => `Reminded ${plural(r.sent, "student")}.`, invalidate: [["cell", "nudge", driveId]],
  });
  const n = q.data?.pending ?? 0;
  return (
    <Card title="Eligible but not applied" subtitle="An automatic reminder goes out a day before the application deadline.">
      <div className="row between">
        <span>
          <strong>{plural(n, "student")}</strong> eligible and not applied yet.
          {q.data?.last_sent_at && <span className="small faint"> Last reminder {clock(q.data.last_sent_at)} to {plural(q.data.last_count ?? 0, "student")}.</span>}
        </span>
        <ConfirmButton size="small" variant="primary" disabled={!n} busy={send.isPending} title={`Remind ${plural(n, "student")}?`}
          confirmLabel="Send reminder" message="Each gets an in-app notice and, if e-mail is set up, an e-mail saying the drive closes soon."
          onConfirm={() => send.mutate(undefined)}>Remind them</ConfirmButton>
      </div>
    </Card>
  );
}

interface WorkHours { day_start: string; day_end: string; breaks: string[]; max_days: number; skip_sundays: boolean }
interface SchedulePlan { slots: number; finish: string | null; solve_ms: number; optimal: boolean; days: { date: string; slots: number }[]; hours: string }

function dayKeyLocal(iso: string): string {
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function ScheduleTab({ data }: { data: DriveDetail }) {
  const d = data.drive;
  const interviews = data.rounds.filter((r) => r.kind === "interview");
  const [roundId, setRoundId] = useState(interviews[0]?.id ?? 0);
  const [start, setStart] = useState(toLocalInput(new Date(Date.now() + 30 * 60_000)));
  const [hours, setHours] = useState<WorkHours | null>(null);
  const defaults = useQuery({ queryKey: ["cell", "work-hours", d.id], queryFn: () => api.get<WorkHours>(`/api/cell/drives/${d.id}/work-hours`) });
  const h: WorkHours = hours ?? defaults.data ?? { day_start: "09:00", day_end: "18:00", breaks: ["13:00-14:00"], max_days: 3, skip_sundays: true };
  const set = (patch: Partial<WorkHours>) => setHours({ ...h, ...patch });
  const lunch = h.breaks[0] ?? "";
  const [lunchFrom, lunchTo] = lunch ? lunch.split("-") : ["13:00", "14:00"];
  const slots = useQuery({ queryKey: ["cell", "slots", d.id], queryFn: () => api.get<Slot[]>(`/api/cell/drives/${d.id}/slots`) });
  const generate = useAction(
    () => api.post<SchedulePlan>(`/api/cell/drives/${d.id}/schedule`, { round_id: roundId, start_at: fromLocalInput(start), ...h }),
    {
      success: (r) => {
        const spread = r.days.length > 1 ? ` over ${r.days.length} days (${r.days.map((x) => x.slots).join(" + ")})` : "";
        return `Scheduled ${plural(r.slots, "candidate")}${spread} conflict-free in ${r.solve_ms} ms${r.optimal ? " (optimal)" : ""}; done by ${dateShort(r.finish)} ${clock(r.finish)}.`;
      },
      invalidate: [["cell"]],
    },
  );
  const byRound = new Map<string, Slot[]>();
  for (const s of slots.data ?? []) byRound.set(s.round, [...(byRound.get(s.round) ?? []), s]);
  return (
    <div className="stack">
      <Card title="Generate a schedule" subtitle="The solver gives every waiting candidate a panel with no clashes, inside working hours. If a round doesn't fit in one day it carries on the next working day.">
        {interviews.length === 0 ? <p className="muted">Add an interview round with panels first.</p> : (
          <div className="stack">
            <div className="row">
              <select value={roundId} onChange={(e) => setRoundId(Number(e.target.value))} style={{ maxWidth: 240 }} aria-label="Round">
                {interviews.map((r) => <option key={r.id} value={r.id}>{r.name} ({plural(r.panels.length, "panel")}, {r.duration_min} min)</option>)}
              </select>
              <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} style={{ maxWidth: 230 }} aria-label="Earliest start" title="Earliest start" />
            </div>
            <div className="row" style={{ alignItems: "flex-end" }}>
              <Field label="Day starts"><input type="time" value={h.day_start} onChange={(e) => set({ day_start: e.target.value })} style={{ maxWidth: 120 }} /></Field>
              <Field label="Day ends"><input type="time" value={h.day_end} onChange={(e) => set({ day_end: e.target.value })} style={{ maxWidth: 120 }} /></Field>
              <label className="row small" style={{ gap: 6, marginBottom: 8 }}>
                <input type="checkbox" checked={Boolean(lunch)} onChange={(e) => set({ breaks: e.target.checked ? ["13:00-14:00"] : [] })} /> Lunch break
              </label>
              {lunch && (
                <>
                  <Field label="From"><input type="time" value={lunchFrom} onChange={(e) => set({ breaks: [`${e.target.value}-${lunchTo}`] })} style={{ maxWidth: 120 }} /></Field>
                  <Field label="To"><input type="time" value={lunchTo} onChange={(e) => set({ breaks: [`${lunchFrom}-${e.target.value}`] })} style={{ maxWidth: 120 }} /></Field>
                </>
              )}
              <Field label="Up to">
                <select value={h.max_days} onChange={(e) => set({ max_days: Number(e.target.value) })} style={{ maxWidth: 110 }}>
                  {[1, 2, 3, 4, 5, 7].map((n) => <option key={n} value={n}>{plural(n, "day")}</option>)}
                </select>
              </Field>
              <label className="row small" style={{ gap: 6, marginBottom: 8 }}>
                <input type="checkbox" checked={h.skip_sundays} onChange={(e) => set({ skip_sundays: e.target.checked })} /> Skip Sundays
              </label>
            </div>
            <div className="row">
              <Button variant="primary" busy={generate.isPending} onClick={() => generate.mutate(undefined)}>Generate schedule</Button>
              <Link className="btn" to={`/cell/drives/${d.id}/control`}>Open control room</Link>
            </div>
          </div>
        )}
      </Card>
      {slots.data && slots.data.length > 0 && (
        <Card title="Timeline" subtitle="Each panel's day with live projections, one strip per day. The red line is now; amber is in the room.">
          <ScheduleBoard slots={slots.data} />
        </Card>
      )}
      {[...byRound.entries()].map(([name, items]) => {
        const days = [...new Set(items.map((s) => dayKeyLocal(s.start_at)))];
        const multi = days.length > 1;
        return (
          <Card key={name} title={name} subtitle={multi ? `${plural(items.length, "slot")} over ${days.length} days, ${dateShort(items[0]?.start_at)} to ${dateShort(items[items.length - 1]?.start_at)}` : `${plural(items.length, "slot")}, ${dateShort(items[0]?.start_at)}`}>
            <div className="table-wrap">
              <table>
                <thead><tr>{multi && <th>Day</th>}<th>Time</th><th>Panel</th><th>Student</th><th>Status</th></tr></thead>
                <tbody>
                  {items.map((s) => (
                    <tr key={s.id}>
                      {multi && <td className="num">{dateShort(s.start_at)}</td>}
                      <td className="ops num">{clock(s.start_at)}–{clock(s.end_at)}</td>
                      <td>{s.panel} <span className="faint small">{s.room}</span></td>
                      <td>{s.student} <span className="faint small">{s.roll_no}</span></td>
                      <td><Badge>{s.status.replace("_", " ")}</Badge>{s.lateness_min > 2 && <span className="small" style={{ color: "var(--amber)", marginLeft: 6 }}>+{s.lateness_min} min</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        );
      })}
    </div>
  );
}

/** Who the rules leave out, and whether any accepted branch is hit disproportionately (four-fifths rule). */
function FairnessTab({ driveId }: { driveId: number }) {
  const q = useQuery({ queryKey: ["cell", "fairness", driveId], queryFn: () => api.get<FairnessAudit>(`/api/cell/drives/${driveId}/fairness`) });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error} />;
  const f = q.data;
  const t = f.totals;
  return (
    <div className="stack">
      <div className="kpi-grid">
        <div className="kpi-tile accent"><div className="v">{t.eligible}</div><div className="l">eligible under these rules</div></div>
        <div className="kpi-tile"><div className="v">{t.rule_excluded}</div><div className="l">excluded by the drive's rules</div></div>
        <div className="kpi-tile"><div className="v">{t.policy_excluded}</div><div className="l">out because of placement policy</div></div>
        <div className="kpi-tile"><div className="v">{f.near_miss.length}</div><div className="l">within {f.near_miss_margin} of the CGPA cutoff</div></div>
      </div>
      <Card title="What the audit found">
        <div className="flag-list">
          {f.flags.map((fl, i) => <div key={i} className={`flag-item ${fl.level}`}>{fl.text}</div>)}
        </div>
      </Card>
      <div className="grid grid-2">
        <Card title="Eligibility rate by branch" subtitle="Branches this drive accepts. Ratio is against the best branch; under 0.80 fails the four-fifths rule.">
          <HBars max={100} rows={f.branches.map((b) => ({
            label: b.branch, value: b.rate,
            note: `${b.eligible}/${b.students} · ${b.ratio === null ? "small group" : `${b.ratio.toFixed(2)}x`}${b.ratio !== null && b.ratio < 0.8 ? " ⚠" : ""}`,
          }))} />
        </Card>
        <Card title="Who each rule excludes" subtitle={'"Only reason" counts students this rule alone keeps out.'}>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Rule</th><th>Excludes</th><th>Only reason</th><th>Hardest hit</th></tr></thead>
              <tbody>
                {f.rules.map((r, i) => (
                  <tr key={r.id ?? i}>
                    <td>{r.label}</td>
                    <td className="num">{r.excluded}</td>
                    <td className="num">{r.sole}</td>
                    <td className="small">{r.by_branch.slice(0, 3).map((b) => `${b.branch} ${b.count}`).join(" · ") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
        {f.sweep.length > 0 && (
          <Card title="Pool size as the CGPA cutoff moves" subtitle="Other rules held fixed. The darker bar is the current cutoff.">
            <BarChart labelEvery={2} data={f.sweep.map((s) => ({
              label: s.cutoff.toFixed(1), value: s.eligible,
              color: s.current ? "var(--signal)" : "var(--tier-regular)", hint: `cutoff ${s.cutoff}: ${s.eligible} eligible`,
            }))} />
          </Card>
        )}
        <Card title="Near misses" subtitle={`Pass every other rule but miss the CGPA cutoff by ${f.near_miss_margin} or less.`}>
          {f.near_miss.length === 0 ? <p className="muted">Nobody is just below the cutoff.</p> : (
            <div className="table-wrap" style={{ maxHeight: 300 }}>
              <table>
                <thead><tr><th>Student</th><th>Branch</th><th>CGPA</th><th>Short by</th></tr></thead>
                <tbody>
                  {f.near_miss.map((n) => (
                    <tr key={n.student_id}>
                      <td>{n.name} <span className="small faint">{n.roll_no}</span></td>
                      <td>{n.branch}</td>
                      <td className="num">{n.cgpa.toFixed(2)}</td>
                      <td className="num">{n.gap.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

function LinksTab({ data }: { data: DriveDetail }) {
  const d = data.drive;
  const links = useQuery({ queryKey: ["cell", "links", d.id], queryFn: () => api.get<{ recruiter: string; panels: { panel_id: number; name: string; round: string; url: string }[] }>(`/api/cell/drives/${d.id}/links`) });
  const offers = useQuery({ queryKey: ["cell", "offers", d.id], queryFn: () => api.get<Offer[]>(`/api/cell/drives/${d.id}/offers`) });
  const rotate = useAction(() => api.post(`/api/cell/drives/${d.id}/links/rotate`), { success: "Links rotated. Old links no longer work.", invalidate: [["cell", "links", d.id]] });
  const copy = useCopyLink();
  const recruiterUrl = links.data?.recruiter;
  // Links stop working once the drive is over (the API refuses them too).
  const finished = d.status === "completed" || d.status === "cancelled";
  const finishedNote = `This drive is ${d.status}, so its links no longer open.`;
  return (
    <div className="grid grid-2">
      <Card title="Recruiter link" subtitle={finished ? finishedNote : "No account needed. Expires in 21 days; rotate it any time."}
        action={!finished && (
          <ConfirmButton size="small" busy={rotate.isPending} title="Rotate all links?" confirmLabel="Rotate links"
            message="The recruiter link and every panel link stop working at once. Send the new links to the recruiter and interviewers."
            onConfirm={() => rotate.mutate(undefined)}>Rotate links</ConfirmButton>
        )}>
        {finished ? (
          <div className="row">
            <Button size="small" disabled title={finishedNote}>Copy link</Button>
            <Button size="small" disabled title={finishedNote}>Open as recruiter</Button>
          </div>
        ) : recruiterUrl && (
          <div className="stack">
            <div className="qr-box"><QRCodeSVG value={recruiterUrl} size={148} /></div>
            <div className="row">
              <Button size="small" onClick={() => copy(recruiterUrl)}>Copy link</Button>
              <a className="btn small" href={recruiterUrl} target="_blank" rel="noreferrer">Open as recruiter</a>
            </div>
          </div>
        )}
      </Card>
      <Card title="Panel links" subtitle={finished ? finishedNote : "Hand one to each interviewer for drive day."}>
        {links.data?.panels.length ? (
          <PanelLinkList links={links.data.panels} heading={`${d.company}: ${d.role} - interview panel links`}
            disabled={finished} disabledNote={finishedNote} showOpen />
        ) : <p className="muted">Add panels to interview rounds to get their links.</p>}
      </Card>
      <Card title="Offers" className="span-2">
        {offers.data?.length ? (
          <table>
            <thead><tr><th>Offer</th><th>CTC</th><th>Status</th></tr></thead>
            <tbody>{offers.data.map((o) => <tr key={o.id}><td>#{o.application_id}</td><td>{lpa(o.ctc_lpa)}</td><td><Badge>{o.status}</Badge></td></tr>)}</tbody>
          </table>
        ) : <p className="muted">Recruiters release offers from their link once candidates clear every round.</p>}
      </Card>
    </div>
  );
}

const NEXT_STATUS: Partial<Record<DriveStatus, { to: DriveStatus; label: string }>> = {
  draft: { to: "open", label: "Open applications" },
  open: { to: "closed", label: "Close applications" },
  closed: { to: "live", label: "Start drive day" },
  live: { to: "completed", label: "Mark completed" },
};

export function CellDrive() {
  const id = Number(useParams().id);
  const [tab, setTab] = useState<Tab>("rules");
  const q = useQuery({ queryKey: ["cell", "drive", id], queryFn: () => api.get<DriveDetail>(`/api/cell/drives/${id}`) });
  const setStatus = useAction((status: DriveStatus) => api.patch(`/api/cell/drives/${id}`, { status }), {
    success: "Drive status updated.", invalidate: [["cell"]],
  });
  const kit = useAction(() => download(`/api/cell/drives/${id}/drive-kit.pdf`, `drive_kit_${id}.pdf`), { success: "Drive-day kit downloaded." });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error} />;
  const d = q.data.drive;
  const next = NEXT_STATUS[d.status];
  return (
    <>
      <p className="small"><Link to="/cell/drives">All drives</Link></p>
      <PageHead title={`${d.company}: ${d.role}`}
        lead={`${lpa(d.ctc_lpa)} (${d.tier}). ${d.eligible ?? 0} eligible, ${d.applicants} applied, target ${d.target_hires} hires.`}
        right={<div className="row"><Badge>{d.status}</Badge>
          {(d.status === "closed" || d.status === "live") && (
            <Button busy={kit.isPending} title="Panel run sheets with QR codes and an attendance sheet, for printing"
              onClick={() => kit.mutate(undefined)}>Drive-day kit (PDF)</Button>
          )}
          {next && <Button variant="primary" busy={setStatus.isPending} onClick={() => setStatus.mutate(next.to)}>{next.label}</Button>}</div>} />
      <Tabs<Tab> value={tab} onChange={setTab} tabs={[
        { id: "rules", label: `Eligibility${d.rules_pending ? ` (${d.rules_pending})` : ""}` },
        { id: "fairness", label: "Fairness audit" },
        { id: "rounds", label: "Rounds and panels" },
        { id: "applicants", label: "Applicants" },
        { id: "schedule", label: "Schedule" },
        { id: "links", label: "Links and offers" },
      ]} />
      {tab === "rules" && <RulesTab data={q.data} />}
      {tab === "fairness" && <FairnessTab driveId={d.id} />}
      {tab === "rounds" && <RoundsTab data={q.data} />}
      {tab === "applicants" && <ApplicantsTab data={q.data} />}
      {tab === "schedule" && <ScheduleTab data={q.data} />}
      {tab === "links" && <LinksTab data={q.data} />}
    </>
  );
}
