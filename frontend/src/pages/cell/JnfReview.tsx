import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../../api/client";
import type { JnfData, JnfDetail } from "../../api/types";
import { Badge, Button, Card, ConfirmButton, Empty, ErrorBox, Field, Loading, PageHead, Stat, useAction } from "../../components/ui";
import { useCopyLink } from "../../lib/clipboard";
import { dateShort, dateTime, lpa, plural, ruleText } from "../../lib/format";
import { JNF_STATUS } from "../../lib/jnf";

type Approved = { drive_id: number; opened: boolean; recruiter_url: string; emailed: boolean };

function Pay({ d }: { d: JnfData }) {
  const parts = [["Base", d.base_lpa], ["Variable", d.variable_lpa], ["Joining bonus", d.joining_bonus_lpa], ["Other", d.other_lpa]]
    .filter(([, v]) => v !== null) as [string, number][];
  return (
    <ul className="plain">
      <li><strong>{lpa(d.ctc_lpa)}</strong> CTC{d.location && <> · {d.location}</>}</li>
      {parts.length > 0 && <li>{parts.map(([k, v]) => `${k} ${v} LPA`).join(" · ")}</li>}
      <li>{d.bond_months ? `Bond of ${plural(d.bond_months, "month")}${d.bond_note ? `: ${d.bond_note}` : ""}` : "No bond"}</li>
    </ul>
  );
}

function Criteria({ d }: { d: JnfData }) {
  const lines = [
    d.cgpa_min !== null && ruleText("cgpa", ">=", d.cgpa_min),
    d.max_backlogs !== null && ruleText("backlogs", "<=", d.max_backlogs),
    d.branches.length > 0 && ruleText("branch", "in", d.branches),
    d.programmes.length > 0 && ruleText("programme", "in", d.programmes),
  ].filter(Boolean) as string[];
  return lines.length ? <ul className="plain">{lines.map((l) => <li key={l}>{l}</li>)}</ul> : <p className="muted">Open to every student.</p>;
}

export function CellJnfReview() {
  const id = Number(useParams().id);
  const nav = useNavigate();
  const copy = useCopyLink();
  const [openNow, setOpenNow] = useState(true);
  const [sendBack, setSendBack] = useState<null | "changes" | "decline">(null);
  const [note, setNote] = useState("");
  const q = useQuery({ queryKey: ["cell", "jnf", id], queryFn: () => api.get<JnfDetail>(`/api/cell/jnf/${id}`) });
  const approve = useAction(() => api.post<Approved>(`/api/cell/jnf/${id}/approve`, { open_now: openNow }), {
    success: (r) =>
      (r.opened ? "Drive created and opened. Eligible students were notified." : "Drive created as a draft. Approve its pending rules to open it.") +
      (r.emailed ? " The recruiter link was e-mailed to the company." : " Send the company its recruiter link from Links and offers."),
    invalidate: [["cell"]],
    onDone: (r) => nav(`/cell/drives/${r.drive_id}`),
  });
  const decide = useAction(
    (kind: "changes" | "decline") => api.post(`/api/cell/jnf/${id}/${kind === "changes" ? "request-changes" : "decline"}`, { note }),
    { success: "Sent to the company.", invalidate: [["cell"]], onDone: () => { setSendBack(null); setNote(""); } },
  );
  const rotate = useAction(() => api.post<{ url: string }>(`/api/cell/jnf/${id}/rotate`), {
    success: "New link made. The old one no longer opens.",
    invalidate: [["cell", "jnf", id]],
  });

  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error} />;
  const { jnf: n, review, url } = q.data;
  const d = n.data;
  const status = JNF_STATUS[n.status];

  return (
    <>
      <PageHead
        title={`${n.company_name}${n.role ? `: ${n.role}` : ""}`}
        lead={<>Job notification form · {n.contact_name ?? "no contact name"}{n.contact_email && <> · {n.contact_email}</>}</>}
        right={
          <div className="row">
            <Badge tone={status.tone}>{status.label}</Badge>
            {n.status !== "approved" && n.status !== "declined" && (
              <>
                <Button size="small" onClick={() => copy(url, "Form link")}>Copy form link</Button>
                <ConfirmButton size="small" variant="default" title="Make a new link?" confirmLabel="Make new link" busy={rotate.isPending}
                  message="The old link stops working at once. Send the new one to the company." onConfirm={() => rotate.mutate(undefined)}>
                  New link
                </ConfirmButton>
              </>
            )}
          </div>
        }
      />
      {!d || !review ? (
        <Card><Empty title={`Waiting for ${n.company_name}`}>The form hasn't been sent yet. Copy the link above if they need it again.</Empty></Card>
      ) : (
        <div className="grid grid-2">
          <Card title="Check before approving" subtitle={n.submitted_at ? `Sent ${dateTime(n.submitted_at)}` : undefined}>
            <div className="row" style={{ gap: 28, marginBottom: 12 }}>
              <Stat value={review.eligible} label={`of ${review.total_students} students eligible`} />
              <Stat value={review.tier} label="tier under the current policy" />
            </div>
            {review.flags.length === 0 ? (
              <p className="muted">Nothing unusual in this form.</p>
            ) : (
              <ul className="flags">{review.flags.map((f) => <li key={f}>{f}</li>)}</ul>
            )}
          </Card>
          <Card title="Role and pay"><Pay d={d} /></Card>
          <Card title="Who can apply"><Criteria d={d} /></Card>
          <Card title="Selection process and dates">
            <ol style={{ margin: 0, paddingLeft: 18 }}>
              {d.rounds.map((r, i) => <li key={i}>{r.name} <span className="small faint">({r.kind}, {r.duration_min} min)</span></li>)}
            </ol>
            <ul className="plain" style={{ marginTop: 10 }}>
              {d.interview_panels !== null && <li>{plural(d.interview_panels, "interview panel")} offered</li>}
              <li>Preferred drive date: {d.preferred_date ? dateShort(d.preferred_date) : "not given"}</li>
              <li>Applications close: {d.apply_by ? dateShort(d.apply_by) : "not given"}</li>
              <li>{plural(d.target_hires, "hire")} expected</li>
            </ul>
          </Card>
          {(d.jd_text || d.notes) && (
            <Card title="Job description and notes" className="span-2">
              {d.notes && <div className="callout" style={{ marginBottom: 10 }}><strong>Note from {n.company_name}:</strong> {d.notes}</div>}
              {d.jd_text && <pre className="jd">{d.jd_text}</pre>}
            </Card>
          )}
          <Card title="Decision" className="span-2">
            {n.status === "submitted" ? (
              <div className="stack">
                <label className="row small" style={{ gap: 8, cursor: "pointer" }}>
                  <input type="checkbox" checked={openNow} onChange={(e) => setOpenNow(e.target.checked)} />
                  Open the drive to students now (it stays a draft if the job description adds rules you still need to approve)
                </label>
                <div className="row">
                  <Button variant="primary" busy={approve.isPending} onClick={() => approve.mutate(undefined)}>Approve and create the drive</Button>
                  <Button onClick={() => setSendBack("changes")}>Ask for changes</Button>
                  <Button variant="danger" onClick={() => setSendBack("decline")}>Decline</Button>
                </div>
                {sendBack && (
                  <div className="stack">
                    <Field label={sendBack === "changes" ? "What should they change? (sent to the company)" : "Why decline? (sent to the company)"}>
                      <textarea value={note} onChange={(e) => setNote(e.target.value)} style={{ minHeight: 80 }} autoFocus />
                    </Field>
                    <div className="row">
                      <Button variant={sendBack === "decline" ? "danger" : "primary"} busy={decide.isPending} disabled={note.trim().length < 3}
                        onClick={() => decide.mutate(sendBack)}>
                        {sendBack === "changes" ? "Send back for changes" : "Decline the form"}
                      </Button>
                      <Button variant="ghost" onClick={() => setSendBack(null)}>Cancel</Button>
                    </div>
                  </div>
                )}
              </div>
            ) : n.status === "approved" && n.drive_id ? (
              <p>Approved by {n.decided_by} {n.decided_at && dateTime(n.decided_at)}. <Link to={`/cell/drives/${n.drive_id}`}>Open the drive</Link>.</p>
            ) : (
              <p className="muted">
                {n.status === "changes_requested" && <>Sent back for changes: “{n.cell_note}”. It returns to your inbox when the company sends it again.</>}
                {n.status === "declined" && <>Declined: “{n.cell_note}”.</>}
                {n.status === "invited" && "Waiting for the company."}
              </p>
            )}
          </Card>
        </div>
      )}
    </>
  );
}
