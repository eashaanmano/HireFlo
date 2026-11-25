import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../../api/client";
import type { JnfCriteria, JnfPool, JnfPublic } from "../../api/types";
import { Button, Card, ErrorBox, Field, Loading, useAction } from "../../components/ui";
import { type JnfDraft, type JnfDraftRound, criteriaOf, draftFrom, jnfPayload } from "../../lib/jnf";

const MAX_ROUNDS = 6;

function StatusNote({ view }: { view: JnfPublic }) {
  switch (view.status) {
    case "submitted":
      return <div className="callout signal">Sent. The placement cell is reviewing it, and you can still change it until they decide.</div>;
    case "changes_requested":
      return <div className="callout warn"><strong>The placement cell asked for a change:</strong> {view.cell_note}</div>;
    case "approved":
      return <div className="callout signal"><strong>Approved.</strong> The placement cell created your drive and will send you your recruiter link for shortlists, results and interview progress.</div>;
    case "declined":
      return <div className="callout bad"><strong>Declined this season:</strong> {view.cell_note}</div>;
    default:
      return null;
  }
}

function CheckList({ label, options, value, onChange }: { label: string; options: string[]; value: string[]; onChange: (v: string[]) => void }) {
  const toggle = (o: string) => onChange(value.includes(o) ? value.filter((x) => x !== o) : [...value, o]);
  return (
    <fieldset className="checks">
      <legend>{label}</legend>
      <div className="row" style={{ gap: 8 }}>
        {options.map((o) => (
          <label key={o} className="chip check">
            <input type="checkbox" checked={value.includes(o)} onChange={() => toggle(o)} /> {o}
          </label>
        ))}
      </div>
      <p className="small faint" style={{ margin: "6px 0 0" }}>{value.length === 0 ? "None ticked: open to all." : `${value.length} selected.`}</p>
    </fieldset>
  );
}

function Rounds({ rounds, onChange }: { rounds: JnfDraftRound[]; onChange: (r: JnfDraftRound[]) => void }) {
  const set = (i: number, patch: Partial<JnfDraftRound>) => onChange(rounds.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <div className="stack">
      {rounds.map((r, i) => (
        <div className="jnf-round" key={i}>
          <span className="small faint">{i + 1}</span>
          <Field label="Round"><input value={r.name} onChange={(e) => set(i, { name: e.target.value })} required /></Field>
          <Field label="Type">
            <select value={r.kind} onChange={(e) => set(i, { kind: e.target.value as JnfDraftRound["kind"] })}>
              <option value="test">Test</option>
              <option value="interview">Interview</option>
            </select>
          </Field>
          <Field label="Minutes"><input type="number" min={10} max={240} value={r.duration_min} onChange={(e) => set(i, { duration_min: e.target.value })} /></Field>
          <Button size="small" variant="ghost" disabled={rounds.length === 1} aria-label={`Remove round ${i + 1}`} onClick={() => onChange(rounds.filter((_, j) => j !== i))}>Remove</Button>
        </div>
      ))}
      <div>
        <Button size="small" disabled={rounds.length >= MAX_ROUNDS} onClick={() => onChange([...rounds, { name: "", kind: "interview", duration_min: "30" }])}>Add a round</Button>
      </div>
    </div>
  );
}

function Editor({ token, view }: { token: string; view: JnfPublic }) {
  const [d, setD] = useState<JnfDraft>(() => draftFrom(view.data));
  const [pool, setPool] = useState<JnfPool | null>(null);
  const set = <K extends keyof JnfDraft>(k: K, v: JnfDraft[K]) => setD((x) => ({ ...x, [k]: v }));
  const criteria = useMemo(() => criteriaOf(d), [d]);
  const criteriaKey = JSON.stringify(criteria);

  useEffect(() => {
    // keyed on the serialised criteria so typing elsewhere in the form doesn't refetch the count
    const c = JSON.parse(criteriaKey) as JnfCriteria | null;
    if (!c) { setPool(null); return; }
    const t = window.setTimeout(() => {
      api.post<JnfPool>("/api/jnf/pool-preview", c, token).then(setPool).catch(() => setPool(null));
    }, 350);
    return () => window.clearTimeout(t);
  }, [criteriaKey, token]);

  const submit = useAction(() => api.post<JnfPublic>("/api/jnf/submit", jnfPayload(d), token), {
    success: "Sent to the placement cell.",
    invalidate: [["jnf", token]],
  });
  const ready = d.role.trim().length >= 2 && criteria !== null && d.rounds.length > 0 && d.rounds.every((r) => r.name.trim().length >= 2);
  const resend = view.status !== "invited";

  return (
    <form className="stack" onSubmit={(e) => { e.preventDefault(); if (ready) submit.mutate(undefined); }}>
      <StatusNote view={view} />
      <fieldset disabled={!view.editable} className="bare stack">
        <Card title="The role and pay" subtitle="The CTC is what the placement policy uses to place the drive in a tier.">
          <div className="stack">
            <div className="form-grid">
              <Field label="Role"><input value={d.role} onChange={(e) => set("role", e.target.value)} placeholder="Silicon Design Engineer" required /></Field>
              <Field label="CTC that counts (LPA)"><input type="number" min={0} step="0.01" value={d.ctc_lpa} onChange={(e) => set("ctc_lpa", e.target.value)} required /></Field>
              <Field label="Location"><input value={d.location} onChange={(e) => set("location", e.target.value)} placeholder="Bengaluru / Hyderabad" /></Field>
            </div>
            <div className="form-grid">
              <Field label="Base (LPA)"><input type="number" min={0} step="0.01" value={d.base_lpa} onChange={(e) => set("base_lpa", e.target.value)} /></Field>
              <Field label="Variable (LPA)"><input type="number" min={0} step="0.01" value={d.variable_lpa} onChange={(e) => set("variable_lpa", e.target.value)} /></Field>
              <Field label="Joining bonus (LPA)"><input type="number" min={0} step="0.01" value={d.joining_bonus_lpa} onChange={(e) => set("joining_bonus_lpa", e.target.value)} /></Field>
              <Field label="Other (LPA)"><input type="number" min={0} step="0.01" value={d.other_lpa} onChange={(e) => set("other_lpa", e.target.value)} /></Field>
            </div>
            <div className="form-grid">
              <Field label="Service bond (months)"><input type="number" min={0} max={60} value={d.bond_months} onChange={(e) => set("bond_months", e.target.value)} /></Field>
              <Field label="Bond details"><input value={d.bond_note} onChange={(e) => set("bond_note", e.target.value)} placeholder="Only if there is a bond" /></Field>
            </div>
          </div>
        </Card>

        <Card title="Who can apply" subtitle="The count updates as you change the criteria. You see numbers only, never students' details.">
          <div className="stack">
            <div className="callout signal" aria-live="polite">
              {pool ? (
                <><strong>{pool.eligible} of {pool.total_students} students</strong> meet these criteria. At this CTC the drive is <strong>{pool.tier}</strong>.</>
              ) : "Enter the CTC to see how many students qualify."}
            </div>
            <CheckList label="Branches" options={view.options.branches} value={d.branches} onChange={(v) => set("branches", v)} />
            <CheckList label="Programmes" options={view.options.programmes} value={d.programmes} onChange={(v) => set("programmes", v)} />
            <div className="form-grid">
              <Field label="Minimum CGPA"><input type="number" min={0} max={10} step="0.01" value={d.cgpa_min} onChange={(e) => set("cgpa_min", e.target.value)} placeholder="No minimum" /></Field>
              <Field label="Most active backlogs allowed"><input type="number" min={0} max={20} value={d.max_backlogs} onChange={(e) => set("max_backlogs", e.target.value)} placeholder="No limit" /></Field>
            </div>
          </div>
        </Card>

        <Card title="Selection process" subtitle="In the order candidates go through them. Tests are taken together; interviews are scheduled per panel.">
          <div className="stack">
            <Rounds rounds={d.rounds} onChange={(r) => set("rounds", r)} />
            <div className="form-grid">
              <Field label="Interview panels you can bring"><input type="number" min={1} max={20} value={d.interview_panels} onChange={(e) => set("interview_panels", e.target.value)} /></Field>
            </div>
          </div>
        </Card>

        <Card title="Dates and numbers">
          <div className="form-grid">
            <Field label="Preferred drive date"><input type="date" value={d.preferred_date} onChange={(e) => set("preferred_date", e.target.value)} /></Field>
            <Field label="Applications close"><input type="date" value={d.apply_by} onChange={(e) => set("apply_by", e.target.value)} /></Field>
            <Field label="Hires you expect"><input type="number" min={1} max={500} value={d.target_hires} onChange={(e) => set("target_hires", e.target.value)} /></Field>
          </div>
        </Card>

        <Card title="Job description and notes">
          <div className="stack">
            <Field label="Job description"><textarea value={d.jd_text} onChange={(e) => set("jd_text", e.target.value)} style={{ minHeight: 160 }} placeholder="Paste the JD. Requirements in it are proposed to the placement cell as extra rules." /></Field>
            <Field label="Notes for the placement cell"><textarea value={d.notes} onChange={(e) => set("notes", e.target.value)} style={{ minHeight: 80 }} /></Field>
          </div>
        </Card>

        {view.editable && (
          <div className="row">
            <Button type="submit" variant="primary" size="big" busy={submit.isPending} disabled={!ready}>
              {resend ? "Send the changes" : "Send to the placement cell"}
            </Button>
            {!ready && <span className="small muted">Add the role, the CTC and a name for every round.</span>}
          </div>
        )}
      </fieldset>
    </form>
  );
}

export function JobNotificationForm() {
  const token = useParams().token ?? "";
  const q = useQuery({ queryKey: ["jnf", token], queryFn: () => api.get<JnfPublic>("/api/jnf", token), retry: false });
  if (q.isLoading) return <div className="main"><Loading /></div>;
  if (q.error || !q.data) return <div className="main"><ErrorBox error={q.error} /></div>;
  const view = q.data;
  return (
    <main className="main recruiter" style={{ margin: "0 auto", maxWidth: 980 }}>
      <section className="hero">
        <div>
          <div className="small" style={{ color: "#9fb3cc", fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase" }}>Job notification form · NIT Calicut placements</div>
          <h1>{view.company_name}</h1>
          <p>Tell the placement cell about the role. As you set who can apply, you'll see how many students qualify. The cell reviews every form before a drive opens; this secure link needs no account.</p>
        </div>
      </section>
      <Editor key={`${view.status}-${view.id}`} token={token} view={view} />
    </main>
  );
}
