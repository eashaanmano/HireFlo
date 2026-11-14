import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api/client";
import type { CompanyProfile, Resume, StudentDriveDetail } from "../../api/types";
import { HBars } from "../../components/charts";
import { Reasons } from "../../components/Reasons";
import { Badge, Button, Card, ConfirmButton, ErrorBox, Field, Loading, Modal, PageHead, useAction } from "../../components/ui";
import { dateTime, lpa, relative } from "../../lib/format";

export function StudentDrive() {
  const id = Number(useParams().id);
  const [disputeOpen, setDisputeOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [resumeId, setResumeId] = useState<number | "">("");
  const detail = useQuery({
    queryKey: ["me", "drive", id],
    queryFn: () => api.get<StudentDriveDetail>(`/api/me/drives/${id}`),
  });
  const resumes = useQuery({ queryKey: ["me", "resumes"], queryFn: () => api.get<Resume[]>("/api/me/resumes") });
  const apply = useAction(() => api.post(`/api/me/drives/${id}/apply`, { resume_id: resumeId || null }), {
    success: "Applied. You'll be notified when you're shortlisted.",
    invalidate: [["me"]],
  });
  const withdraw = useAction(() => api.post(`/api/me/drives/${id}/withdraw`), {
    success: "Application withdrawn.",
    invalidate: [["me"]],
  });
  const dispute = useAction(() => api.post("/api/me/disputes", { drive_id: id, message }), {
    success: "Dispute sent to the placement cell.",
    invalidate: [["me", "drive", id]],
    onDone: () => setDisputeOpen(false),
  });

  if (detail.isLoading) return <Loading />;
  if (detail.error || !detail.data) return <ErrorBox error={detail.error} />;
  const { drive: d, eligible, reasons, application, rounds, experience } = detail.data;
  const active = application && ["applied", "in_process"].includes(application.status);

  return (
    <>
      <p className="small"><Link to="/student/drives">All drives</Link></p>
      <PageHead
        title={`${d.company}: ${d.role}`}
        lead={`${lpa(d.ctc_lpa)} (${d.tier})${d.location ? `, ${d.location}` : ""}. Applications close ${dateTime(d.apply_deadline)} (${relative(d.apply_deadline)}).`}
        right={<Badge>{d.status}</Badge>}
      />
      <div className="grid grid-2">
        <Card title={eligible ? "You're eligible" : "You're not eligible"} subtitle="Every check, with the rule behind it.">
          <Reasons reasons={reasons} />
          <hr className="divider" />
          {application ? (
            <div className="row between">
              <span>Your application: <Badge tone="info">{application.stage}</Badge></span>
              {active && (
                <ConfirmButton size="small" busy={withdraw.isPending} title="Withdraw your application?" confirmLabel="Withdraw"
                  message="Your interview slots are released. You can apply again only until the application deadline."
                  onConfirm={() => withdraw.mutate(undefined)}>Withdraw</ConfirmButton>
              )}
            </div>
          ) : eligible && d.status === "open" ? (
            <div className="row">
              <select value={resumeId} onChange={(e) => setResumeId(e.target.value ? Number(e.target.value) : "")} style={{ maxWidth: 260 }} aria-label="Resume">
                <option value="">Default resume</option>
                {resumes.data?.map((r) => (
                  <option key={r.id} value={r.id}>{r.track}: {r.filename}</option>
                ))}
              </select>
              <Button variant="primary" busy={apply.isPending} onClick={() => apply.mutate(undefined)}>
                Apply
              </Button>
            </div>
          ) : !eligible ? (
            <div className="stack">
              {detail.data.dispute ? (
                <div className="callout">
                  Dispute {detail.data.dispute.status}.{" "}
                  {detail.data.dispute.resolution ?? "The placement cell will review it."}
                </div>
              ) : (
                <Button onClick={() => setDisputeOpen(true)}>Dispute this decision</Button>
              )}
            </div>
          ) : null}
        </Card>

        <Card title="How the drive runs">
          <ol style={{ margin: 0, paddingLeft: 20 }}>
            {rounds.map((r) => (
              <li key={r.seq} style={{ marginBottom: 4 }}>
                {r.name} <span className="faint small">({r.kind}, about {r.duration_min} min)</span>
              </li>
            ))}
          </ol>
          {experience.count > 0 && (
            <div className="callout" style={{ marginTop: 14 }}>
              <strong>From seniors' experiences</strong> ({experience.count}): average difficulty{" "}
              {experience.avg_difficulty}/5. Common topics: {experience.topics.map(([t]) => t).join(", ")}.
            </div>
          )}
          {d.skills.length > 0 && (
            <div style={{ marginTop: 14 }}>
              <h3>Skills in the JD</h3>
              <div className="row" style={{ marginTop: 6, gap: 6 }}>
                {d.skills.map((s) => <span className="chip" key={s}>{s}</span>)}
              </div>
            </div>
          )}
        </Card>

        <CompanyInsights driveId={id} />

        <Card title="Job description" className="span-2">
          <pre className="jd">{d.jd_text || "The recruiter hasn't shared a JD yet."}</pre>
        </Card>
      </div>

      {disputeOpen && (
        <Modal title="Dispute this decision" onClose={() => setDisputeOpen(false)}>
          <div className="stack">
            <p className="muted">Explain what's wrong (for example, a backlog that's already cleared). The placement cell sees the exact checks that failed.</p>
            <Field label="Your message">
              <textarea value={message} onChange={(e) => setMessage(e.target.value)} minLength={10} maxLength={2000} />
            </Field>
            <div className="row">
              <Button variant="primary" busy={dispute.isPending} disabled={message.trim().length < 10}
                onClick={() => dispute.mutate(undefined)}>
                Send dispute
              </Button>
              <Button variant="ghost" onClick={() => setDisputeOpen(false)}>Cancel</Button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}

/** FUT-4: what to expect from this company, from its past drives at NITC (aggregates only). */
function CompanyInsights({ driveId }: { driveId: number }) {
  const q = useQuery({ queryKey: ["me", "company-profile", driveId], queryFn: () => api.get<CompanyProfile>(`/api/me/drives/${driveId}/company-profile`) });
  const p = q.data;
  if (q.isLoading) return <Card title="Company insights"><Loading /></Card>;
  if (!p) return null;
  if (!p.has_history) {
    return (
      <Card title={`${p.company.name} at NITC`} subtitle="Company insights">
        <p className="muted">This is the first recorded drive by this company, so there is no history to show yet.</p>
      </Card>
    );
  }
  return (
    <Card title={`${p.company.name} at NITC`} subtitle={`From ${p.drives} past drive${p.drives > 1 ? "s" : ""} (${p.seasons.join(", ")}). Totals only, no individual students.`}>
      <div className="stack" style={{ gap: 10 }}>
        <div className="row" style={{ gap: 6 }}>
          {p.ctc && <span className="chip">{p.ctc.min === p.ctc.max ? `${p.ctc.min} LPA` : `${p.ctc.min}–${p.ctc.max} LPA`}</span>}
          {p.roles.map((r) => <span className="chip" key={r}>{r}</span>)}
        </div>
        {p.rounds.length > 0 && <p className="small">Rounds last time: {p.rounds.map((r) => r.name).join(" → ")}</p>}
        <div>
          <div className="small muted" style={{ marginBottom: 6 }}>How far candidates got (share who cleared each stage)</div>
          <HBars max={100} rows={p.stages.map((s) => ({ label: s.stage, value: s.rate ?? 0, note: s.rate === null ? `${s.count}` : `${s.count} · ${s.rate}%` }))} />
        </div>
        {p.branches_hired.length > 0 && (
          <p className="small">Branches selected: {p.branches_hired.map((b) => `${b.branch} (${b.selected})`).join(", ")}</p>
        )}
      </div>
    </Card>
  );
}
