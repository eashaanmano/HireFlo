import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { DisputeRow, InboxItem, Stats } from "../../api/types";
import { Reasons } from "../../components/Reasons";
import { Badge, Button, Card, Empty, ErrorBox, Field, Loading, Modal, PageHead, Stat, useAction } from "../../components/ui";
import { relative } from "../../lib/format";

const KIND_LABEL: Record<InboxItem["kind"], string> = {
  replan: "Re-plan",
  absent: "Arrival",
  dispute: "Dispute",
  rules: "Rules",
  followup: "Follow-up",
};

function DisputeModal({ id, onClose }: { id: number; onClose: () => void }) {
  const list = useQuery({ queryKey: ["cell", "disputes"], queryFn: () => api.get<DisputeRow[]>("/api/cell/disputes?status=open") });
  const [resolution, setResolution] = useState("");
  const resolve = useAction(
    (status: "upheld" | "rejected") => api.post(`/api/cell/disputes/${id}/resolve`, { status, resolution }),
    { success: "Dispute resolved and the student notified.", invalidate: [["cell"]], onDone: onClose },
  );
  const d = list.data?.find((x) => x.id === id);
  return (
    <Modal title="Review dispute" onClose={onClose}>
      {!d ? (
        <Loading />
      ) : (
        <div className="stack">
          <p><strong>{d.student}</strong> ({d.roll_no}) on <strong>{d.drive}</strong></p>
          <div className="callout">{d.message}</div>
          <div>
            <h3>Checks that failed</h3>
            <Reasons reasons={d.reasons} onlyFailed />
          </div>
          <p className="small muted">
            If the student is right, correct their record from Students (for example, backlogs) and uphold. Eligibility is recomputed automatically.
          </p>
          <Field label="Resolution note (sent to the student)">
            <textarea value={resolution} onChange={(e) => setResolution(e.target.value)} style={{ minHeight: 80 }} />
          </Field>
          <div className="row">
            <Button variant="primary" busy={resolve.isPending} disabled={resolution.trim().length < 3} onClick={() => resolve.mutate("upheld")}>Uphold</Button>
            <Button busy={resolve.isPending} disabled={resolution.trim().length < 3} onClick={() => resolve.mutate("rejected")}>Reject</Button>
            <Link className="btn ghost" to={`/cell/students?q=${d.roll_no}`}>Open student</Link>
          </div>
        </div>
      )}
    </Modal>
  );
}

export function CellInbox() {
  const inbox = useQuery({ queryKey: ["cell", "inbox"], queryFn: () => api.get<InboxItem[]>("/api/cell/inbox"), refetchInterval: 30_000 });
  const stats = useQuery({ queryKey: ["cell", "stats"], queryFn: () => api.get<Stats>("/api/cell/stats") });
  const [dispute, setDispute] = useState<number | null>(null);
  const approve = useAction((p: { drive: number; id: number }) => api.post(`/api/cell/drives/${p.drive}/proposals/${p.id}/approve`), {
    success: "Re-plan applied. Affected students were notified.",
    invalidate: [["cell"]],
  });

  if (inbox.isLoading) return <Loading />;
  if (inbox.error || !inbox.data) return <ErrorBox error={inbox.error} />;
  const items = inbox.data;

  const actionFor = (i: InboxItem) => {
    switch (i.kind) {
      case "replan":
        return (
          <div className="row">
            <Button size="small" variant="primary" busy={approve.isPending} onClick={() => approve.mutate({ drive: i.drive_id, id: i.id })}>Approve</Button>
            <Link className="btn small" to={`/cell/drives/${i.drive_id}/control`}>Review</Link>
          </div>
        );
      case "absent":
        return <Link className="btn small" to={`/cell/drives/${i.drive_id}/control`}>Control room</Link>;
      case "dispute":
        return <Button size="small" onClick={() => setDispute(i.id)}>Review</Button>;
      case "rules":
        return <Link className="btn small" to={`/cell/drives/${i.drive_id}`}>Review rules</Link>;
      default:
        return <Link className="btn small" to={`/cell/drives/${i.drive_id}`}>Open drive</Link>;
    }
  };

  return (
    <>
      <PageHead title="Needs your attention" lead="Everything routine is automated. Only exceptions land here." />
      {stats.data && (
        <div className="grid grid-3" style={{ marginBottom: 16 }}>
          <Card><Stat value={`${stats.data.placed_pct}%`} label={`${stats.data.placed} of ${stats.data.students} students placed`} /></Card>
          <Card><Stat value={stats.data.active_drives} label="Drives open or running" /></Card>
          <Card><Stat value={items.filter((i) => i.severity === "high").length} label="Urgent items" /></Card>
        </div>
      )}
      <Card>
        {items.length === 0 ? (
          <Empty title="Inbox zero">Disputes, uncertain rules, late arrivals and re-plans will show up here.</Empty>
        ) : (
          items.map((i) => (
            <div className="inbox-item" key={`${i.kind}-${i.id}`}>
              <span className={`sev ${i.severity}`} aria-label={`${i.severity} priority`} />
              <div>
                <div className="row" style={{ gap: 8 }}>
                  <Badge>{KIND_LABEL[i.kind]}</Badge>
                  <strong>{i.title}</strong>
                </div>
                <div className="small muted" style={{ marginTop: 4 }}>{i.detail}</div>
                <div className="small faint">{relative(i.at)}</div>
              </div>
              {actionFor(i)}
            </div>
          ))
        )}
      </Card>
      {dispute !== null && <DisputeModal id={dispute} onClose={() => setDispute(null)} />}
    </>
  );
}
