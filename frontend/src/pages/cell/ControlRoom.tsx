import { useQuery } from "@tanstack/react-query";
import { Ban, Clock3, UserX, Zap } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api/client";
import type { ControlRoom, ControlRoomPanel, DisruptionKind, DisruptResult, Proposal, Slot } from "../../api/types";
import { ScheduleBoard } from "../../components/ScheduleBoard";
import { Button, Card, ErrorBox, Field, Loading, PageHead, useAction } from "../../components/ui";
import { clock, plural } from "../../lib/format";

function PanelBoard({ driveId, p }: { driveId: number; p: ControlRoomPanel }) {
  const act = useAction(
    ({ slot, action, result }: { slot: Slot; action: string; result?: "pass" | "fail" }) =>
      api.post(`/api/cell/drives/${driveId}/slots/${slot.id}/${action}`, result ? { result } : undefined),
    { invalidate: [["cell", "control", driveId]] },
  );
  const next = p.queue[0];
  return (
    <section className="board" aria-label={p.name}>
      <h3>
        <span>{p.name} · {p.room}</span>
        <span>{p.hold ? <span className="hold-flag">{p.hold.until ? `ON HOLD TO ${clock(p.hold.until)}` : "DOWN"}</span> : p.round}</span>
      </h3>
      <div className="row between" style={{ marginTop: 6 }}>
        <div>
          <div className="label">Finishes about</div>
          <div className="big">{p.finish ? clock(p.finish) : "—"}</div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div className="label">{p.done} done · {p.queue.length} waiting</div>
          {p.late_min > 2 ? <div style={{ color: "#ff9a8a", fontSize: 18 }}>{p.late_min} min behind</div> : <div style={{ color: "#8fd19e" }}>On time</div>}
        </div>
      </div>
      {p.now ? (
        <div className="board-row now">
          <span className="t">NOW</span>
          <span>{p.now.student}</span>
          <span className="row" style={{ gap: 6 }}>
            <Button size="small" variant="primary" onClick={() => p.now && act.mutate({ slot: p.now, action: "complete", result: "pass" })}>Pass</Button>
            <Button size="small" onClick={() => p.now && act.mutate({ slot: p.now, action: "complete", result: "fail" })}>Fail</Button>
          </span>
        </div>
      ) : next ? (
        <div className="board-row">
          <span className="t">NEXT</span>
          <span>{next.student}</span>
          <Button size="small" variant="primary" onClick={() => act.mutate({ slot: next, action: "start" })}>Start</Button>
        </div>
      ) : (
        <div className="board-row"><span className="t">—</span><span>Queue clear</span><span /></div>
      )}
      {p.queue.slice(p.now ? 0 : 1, 6).map((s) => (
        <div className="board-row" key={s.id}>
          <span className="t">{clock(s.projected_start ?? s.start_at)}</span>
          <span>
            {s.student}
            {s.lateness_min > 2 && <span className="late"> +{s.lateness_min}</span>}
          </span>
          <span className="row" style={{ gap: 6 }}>
            {s.checked_in ? <span className="flag">IN</span> : (
              <>
                <Button size="small" onClick={() => act.mutate({ slot: s, action: "checkin" })}>Check in</Button>
                <Button size="small" onClick={() => act.mutate({ slot: s, action: "no-show" })}>Absent</Button>
              </>
            )}
          </span>
        </div>
      ))}
      {p.queue.length > 6 && <div className="label" style={{ marginTop: 6 }}>+{p.queue.length - 6} more</div>}
    </section>
  );
}

function ProposalCard({ driveId, proposal }: { driveId: number; proposal: Proposal }) {
  const decide = useAction((d: "approve" | "dismiss") => api.post(`/api/cell/drives/${driveId}/proposals/${proposal.id}/${d}`), {
    success: (r) => ((r as { status: string }).status === "approved" ? "Re-plan applied; students notified." : "Proposal dismissed."),
    invalidate: [["cell"]],
  });
  const m = proposal.metrics;
  return (
    <Card title="Re-plan ready" subtitle={`${proposal.reason}.`}
      action={<div className="row">
        <Button variant="primary" busy={decide.isPending} onClick={() => decide.mutate("approve")}>Approve</Button>
        <Button onClick={() => decide.mutate("dismiss")}>Dismiss</Button>
      </div>}>
      <p>
        Finish moves from <strong>{clock(m.finish_before)}</strong> to <strong>{clock(m.finish_after)}</strong>
        {m.saved_min > 0 ? ` (${m.saved_min} min earlier)` : ""}. Worst delay drops from {m.max_lateness_before_min} to{" "}
        {m.max_lateness_after_min} min. Moves {plural(proposal.changes.length, "candidate")}.
      </p>
      <ChangesTable changes={proposal.changes} />
    </Card>
  );
}

function ChangesTable({ changes }: { changes: Proposal["changes"] }) {
  return (
    <div className="table-wrap" style={{ marginTop: 10 }}>
      <table>
        <thead><tr><th>Student</th><th>From</th><th>To</th></tr></thead>
        <tbody>
          {changes.map((c) => (
            <tr key={c.slot_id}>
              <td>{c.student} <span className="small faint">{c.round}</span></td>
              <td className="num">{c.from_panel}, {clock(c.from_start)}</td>
              <td className="num"><strong>{c.to_panel}, {clock(c.to_start)}</strong></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Inject a real-world disruption and watch CP-SAT re-plan the day; every screen updates over WebSockets. */
function DisruptionLab({ driveId, panels }: { driveId: number; panels: ControlRoomPanel[] }) {
  const [panelId, setPanelId] = useState<number>(panels[0]?.id ?? 0);
  const [minutes, setMinutes] = useState(40);
  const [autoApply, setAutoApply] = useState(true);
  const [last, setLast] = useState<DisruptResult | null>(null);
  const fire = useAction(
    ({ kind, panel }: { kind: DisruptionKind; panel?: number }) => api.post<DisruptResult>(`/api/cell/drives/${driveId}/disrupt`, {
      kind, panel_id: kind === "no_show" ? null : panel ?? panelId, minutes, auto_apply: autoApply,
    }),
    { invalidate: [["cell"]], onDone: (r) => setLast(r) },
  );
  const held = panels.filter((p) => p.hold);
  const m = last?.metrics;
  return (
    <Card title={<span className="row" style={{ gap: 8 }}><Zap size={18} aria-hidden /> Disruption lab</span>}
      subtitle="Break the day on purpose. The solver re-plans every remaining slot within seconds, and students, recruiters and panels see the new plan live."
      className="span-2">
      <div className="row" style={{ alignItems: "flex-end", gap: 12 }}>
        <Field label="Panel">
          <select value={panelId} onChange={(e) => setPanelId(Number(e.target.value))} style={{ minWidth: 180 }}>
            {panels.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.round}</option>)}
          </select>
        </Field>
        <Field label={`Overrun: ${minutes} min`}>
          <input type="range" min={10} max={120} step={5} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} style={{ width: 160 }} />
        </Field>
        <label className="row small" style={{ gap: 6, paddingBottom: 8 }}>
          <input type="checkbox" checked={autoApply} onChange={(e) => setAutoApply(e.target.checked)} /> Apply re-plan automatically
        </label>
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        <Button busy={fire.isPending} onClick={() => fire.mutate({ kind: "overrun" })}><Clock3 size={15} aria-hidden /> Panel runs {minutes} min over</Button>
        <Button busy={fire.isPending} variant="danger" onClick={() => fire.mutate({ kind: "panel_down" })}><Ban size={15} aria-hidden /> Panel goes down</Button>
        <Button busy={fire.isPending} onClick={() => fire.mutate({ kind: "no_show" })}><UserX size={15} aria-hidden /> Next candidate no-shows</Button>
        {held.map((p) => (
          <Button key={p.id} size="small" variant="ghost" busy={fire.isPending} onClick={() => fire.mutate({ kind: "release", panel: p.id })}>
            Bring back {p.name}
          </Button>
        ))}
      </div>
      {last && (
        <div className={`disrupt-result ${last.applied ? "ok" : ""}`}>
          <div className="disrupt-head">
            <strong>{last.disruption}.</strong>
            <span className="disrupt-ms">{last.solve_ms} ms</span>
          </div>
          {m ? (
            <>
              <p>
                {last.applied ? `Re-planned and applied: ${plural(last.applied, "candidate")} moved and notified.` : "Re-plan drafted; approve it above."}{" "}
                Without re-planning the worst wait was <strong>{m.max_lateness_before_min} min</strong>; now it's{" "}
                <strong>{m.max_lateness_after_min} min</strong>. Day ends {clock(m.finish_after)}
                {m.saved_min > 0 ? `, ${m.saved_min} min earlier than it would have` : ""}.
              </p>
              <ChangesTable changes={last.changes} />
            </>
          ) : <p>The current plan already absorbs this. Nobody needs to move.</p>}
        </div>
      )}
    </Card>
  );
}

function LiveTimeline({ driveId }: { driveId: number }) {
  const slots = useQuery({ queryKey: ["cell", "slots", driveId], queryFn: () => api.get<Slot[]>(`/api/cell/drives/${driveId}/slots`), refetchInterval: 20_000 });
  if (!slots.data?.length) return null;
  return (
    <Card title="Live timeline" subtitle="Watch the bars move when you inject a disruption. The red line is now." className="span-2">
      <ScheduleBoard slots={slots.data} />
    </Card>
  );
}

function VenueQr({ driveId }: { driveId: number }) {
  const code = useQuery({
    queryKey: ["cell", "checkin", driveId],
    queryFn: () => api.get<{ url: string; expires_at: string }>(`/api/cell/drives/${driveId}/checkin-code`),
    refetchInterval: 60_000,
  });
  return (
    <Card title="Venue check-in" subtitle="Show this on the screen at the entrance. It refreshes every minute so it can't be shared.">
      <div className="row" style={{ alignItems: "flex-start", gap: 18 }}>
        <div className="qr-box">{code.data ? <QRCodeSVG value={code.data.url} size={168} /> : <Loading />}</div>
        <p className="small muted" style={{ maxWidth: 260 }}>
          Students scan with their phone camera, sign in if needed, and they're checked in to their next slot. Their live
          queue position appears on their home screen.
        </p>
      </div>
    </Card>
  );
}

export function ControlRoomPage() {
  const id = Number(useParams().id);
  const q = useQuery({
    queryKey: ["cell", "control", id],
    queryFn: () => api.get<ControlRoom>(`/api/cell/drives/${id}/control-room`),
    refetchInterval: 20_000,
  });
  const replan = useAction(() => api.post<{ proposal: number | null; message?: string }>(`/api/cell/drives/${id}/replan`), {
    success: (r) => r.message ?? "Re-plan drafted. Review it below.",
    invalidate: [["cell"]],
  });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error} />;
  const cr = q.data;
  const pending = cr.proposals.filter((p) => p.status === "pending");
  return (
    <>
      <p className="small"><Link to={`/cell/drives/${id}`}>Drive setup</Link></p>
      <PageHead title={`Control room: ${cr.drive.company}`}
        lead={`${cr.progress.done} of ${cr.progress.total} interviews done, ${cr.progress.checked_in} checked in and waiting.`}
        right={<Button busy={replan.isPending} onClick={() => replan.mutate(undefined)}>Re-plan now</Button>} />
      {pending.map((p) => <div key={p.id} style={{ marginBottom: 16 }}><ProposalCard driveId={id} proposal={p} /></div>)}
      {cr.panels.length === 0 ? (
        <Card><p className="muted">Add interview panels and generate a schedule to run the day from here.</p></Card>
      ) : (
        <div className="board-grid">{cr.panels.map((p) => <PanelBoard key={p.id} driveId={id} p={p} />)}</div>
      )}
      <div className="grid grid-2" style={{ marginTop: 16 }}>
        {cr.panels.length > 0 && <DisruptionLab driveId={id} panels={cr.panels} />}
        <LiveTimeline driveId={id} />
        <VenueQr driveId={id} />
        <Card title="Marked absent" subtitle="If they arrive later, check them in and a re-plan is drafted automatically.">
          {cr.no_shows.length === 0 ? <p className="muted">Nobody so far.</p> : cr.no_shows.map((s) => (
            <div key={s.id} className="row between" style={{ padding: "4px 0" }}>
              <span>{s.student} <span className="small faint">{s.round}, {s.panel}</span></span>
              <span className="small faint">planned {clock(s.start_at)}</span>
            </div>
          ))}
        </Card>
      </div>
    </>
  );
}
