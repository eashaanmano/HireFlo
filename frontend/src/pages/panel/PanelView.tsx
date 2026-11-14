import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, download } from "../../api/client";
import type { PanelQueue, PanelQueueCard } from "../../api/types";
import { Button, ErrorBox, Loading, useAction } from "../../components/ui";
import { clock, dateShort } from "../../lib/format";
import { useLive } from "../../lib/live";

/** "10:30" today, "17 Nov 10:30" for a candidate on a later day of a multi-day drive. */
function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toDateString() === new Date().toDateString() ? clock(iso) : `${dateShort(iso)} ${clock(iso)}`;
}

function Elapsed({ since }: { since: string | null }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const t = window.setInterval(() => tick((x) => x + 1), 15_000);
    return () => window.clearInterval(t);
  }, []);
  if (!since) return null;
  return <>{Math.max(0, Math.round((Date.now() - new Date(since).getTime()) / 60000))} min</>;
}

function Candidate({ c, token, skills }: { c: PanelQueueCard; token: string; skills: string[] }) {
  const have = new Set(c.skills.map((s) => s.toLowerCase()));
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="big" style={{ fontSize: 34 }}>{c.student}</div>
      <div className="label">{c.roll_no} · {c.programme} {c.branch} · CGPA {c.cgpa.toFixed(2)}</div>
      <div className="row" style={{ gap: 6 }}>
        {skills.map((s) => (
          <span key={s} className="flag" style={{ opacity: have.has(s) ? 1 : 0.35 }}>{s}</span>
        ))}
      </div>
      {c.has_resume && (
        <div>
          <Button size="small" onClick={() => void download(`/api/p/slots/${c.id}/resume`, `${c.roll_no}.pdf`, token)}>Open resume</Button>
        </div>
      )}
    </div>
  );
}

/** One screen for an interviewer on a phone or laptop. Tapping Done moves the student queue and control room. */
export function PanelView() {
  const token = useParams().token ?? "";
  const live = useLive(Boolean(token), token);
  const q = useQuery({ queryKey: ["p", token], queryFn: () => api.get<PanelQueue>("/api/p/queue", token), retry: false, refetchInterval: 30_000 });
  const act = useAction(
    (p: { id: number; action: "start" | "complete" | "no-show"; result?: "pass" | "fail" }) =>
      api.post(`/api/p/slots/${p.id}/${p.action}`, p.result ? { result: p.result } : undefined, token),
    { invalidate: [["p", token]] },
  );
  if (q.isLoading) return <div className="main"><Loading /></div>;
  if (q.error || !q.data) return <div className="main"><ErrorBox error={q.error} /></div>;
  const { panel, drive, current, waiting, done } = q.data;
  const next = waiting[0];
  const queued = current ? waiting.length : Math.max(0, waiting.length - 1);
  return (
    <div style={{ minHeight: "100%", background: "var(--board)" }}>
      <main className="main" style={{ margin: "0 auto", maxWidth: 760 }}>
        <section className="board" style={{ padding: 0 }}>
          <h3 style={{ padding: "0 0 12px" }}>
            <span>{drive.company} · {panel.round}</span>
            <span className={`live-dot ${live === "live" ? "live" : ""}`} style={{ color: "#9fb3cc" }}>{panel.name}, {panel.room}</span>
          </h3>
          {current ? (
            <div className="stack" style={{ gap: 16 }}>
              <div className="label">In the room · <Elapsed since={current.projected_start} /> of {panel.duration_min} min</div>
              <Candidate c={current} token={token} skills={drive.skills} />
              <div className="row" style={{ marginTop: 8 }}>
                <Button size="big" variant="primary" busy={act.isPending} onClick={() => act.mutate({ id: current.id, action: "complete", result: "pass" })}>Done: clears round</Button>
                <Button size="big" busy={act.isPending} onClick={() => act.mutate({ id: current.id, action: "complete", result: "fail" })}>Done: doesn't clear</Button>
              </div>
            </div>
          ) : next ? (
            <div className="stack" style={{ gap: 16 }}>
              <div className="label">Next up · planned {when(next.start_at)} {next.checked_in ? "· checked in" : "· not checked in yet"}</div>
              <Candidate c={next} token={token} skills={drive.skills} />
              <div className="row">
                <Button size="big" variant="primary" busy={act.isPending} onClick={() => act.mutate({ id: next.id, action: "start" })}>Start interview</Button>
                <Button size="big" busy={act.isPending} onClick={() => act.mutate({ id: next.id, action: "no-show" })}>Not here</Button>
              </div>
            </div>
          ) : (
            <div className="big" style={{ padding: "20px 0" }}>Queue clear. Thank you.</div>
          )}
          <div style={{ marginTop: 22 }}>
            <div className="label">Waiting ({queued})</div>
            {waiting.slice(current ? 0 : 1, 8).map((w) => (
              <div className="board-row" key={w.id}>
                <span className="t">{when(w.projected_start ?? w.start_at)}</span>
                <span>{w.student}</span>
                <span>{w.checked_in ? <span className="flag">IN</span> : ""}</span>
              </div>
            ))}
            <div className="label" style={{ marginTop: 14 }}>{done.length} done today</div>
          </div>
        </section>
      </main>
    </div>
  );
}
