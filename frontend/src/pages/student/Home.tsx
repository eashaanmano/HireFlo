import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { LiveItem, StudentAnalytics, StudentDashboard } from "../../api/types";
import { Funnel, HBars } from "../../components/charts";
import { BarChart, CutoffStrip, Legend, LineChart, tierColor } from "../../components/viz";
import { Badge, Card, Empty, ErrorBox, Loading } from "../../components/ui";
import { clock, plural, relative } from "../../lib/format";

/** "Today", "Tomorrow" or the weekday and date, for an interview start. */
function dayLabel(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(d) - day(now)) / 86400_000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

function DriveDay({ items }: { items: LiveItem[] }) {
  return (
    <section className="board" aria-label="Drive day">
      {items.map((it) => {
        const lateMin = Math.round((new Date(it.eta).getTime() - new Date(it.planned_start).getTime()) / 60000);
        // the live queue only means something on the day; further out, show when and where
        const later = it.status === "scheduled" && new Date(it.planned_start).getTime() - Date.now() > 12 * 3600_000;
        if (later) {
          return (
            <div key={it.slot_id} className="stack" style={{ gap: 8 }}>
              <h3>
                <span>Up next · {it.company} · {it.round}</span>
                <span>{dayLabel(it.planned_start)}</span>
              </h3>
              <div className="row" style={{ gap: 28, alignItems: "flex-end" }}>
                <div>
                  <div className="label">Starts</div>
                  <div className="big">{clock(it.planned_start)}</div>
                </div>
                <div>
                  <div className="label">Where</div>
                  <div style={{ fontSize: 22 }}>{it.panel}, {it.room}</div>
                </div>
              </div>
              <div className="label">Your live queue position and ETA appear here on the day. Scan the venue QR when you arrive.</div>
            </div>
          );
        }
        return (
          <div key={it.slot_id} className="stack" style={{ gap: 8 }}>
            <h3>
              <span>{it.company} · {it.round}</span>
              <span>{it.checked_in ? <span className="flag">Checked in</span> : "Not checked in"}</span>
            </h3>
            <div className="row" style={{ gap: 28, alignItems: "flex-end" }}>
              <div>
                <div className="label">{it.status === "in_progress" ? "In the room now" : "Estimated start"}</div>
                <div className="big">{it.status === "in_progress" ? "NOW" : clock(it.eta)}</div>
              </div>
              {it.status === "scheduled" && (
                <div>
                  <div className="label">Ahead of you</div>
                  <div className="big">{it.ahead}</div>
                </div>
              )}
              <div>
                <div className="label">Where</div>
                <div style={{ fontSize: 22 }}>{it.panel}, {it.room}</div>
              </div>
            </div>
            <div className="label">
              Planned for {clock(it.planned_start)}
              {lateMin > 2 ? `; running about ${lateMin} min behind` : "; on time"}.
              {!it.checked_in && " Scan the QR at the venue when you arrive."}
            </div>
          </div>
        );
      })}
    </section>
  );
}

function StandingCharts() {
  const q = useQuery({ queryKey: ["me", "analytics"], queryFn: () => api.get<StudentAnalytics>("/api/me/analytics") });
  if (q.isLoading) return <Card title="Where you stand"><Loading /></Card>;
  if (!q.data) return null;
  const a = q.data;
  const tiers = [...new Set(a.cutoffs.map((c) => c.tier))];
  return (
    <div className="grid grid-2" style={{ marginTop: 16 }}>
      <Card title="Where you stand against every cutoff" className="span-2"
        subtitle={`Your CGPA ${a.cgpa.toFixed(2)} (teal line) against each drive's minimum this season, highest CTC first.`}>
        <CutoffStrip mine={a.cgpa} min={5} max={10} rows={a.cutoffs.slice(0, 14).map((c) => ({
          label: c.company, value: c.cutoff, color: tierColor(c.tier), ok: c.eligible,
          sub: `${c.role} · ${c.ctc_lpa} LPA`, href: `/student/drives/${c.drive_id}`,
        }))} />
        <Legend items={tiers.map((t) => ({ label: t, color: tierColor(t) }))} />
      </Card>
      <Card title="Drives open to you, by tier" subtitle={a.eligible_ctc.count
        ? `${a.eligible_ctc.count} eligible drives; median ${a.eligible_ctc.median} LPA, best ${a.eligible_ctc.max} LPA.`
        : "No eligible drives this season yet."}>
        <BarChart unit="" data={a.by_tier.flatMap((t) => [
          { label: `${t.tier}`, value: t.eligible, color: tierColor(t.tier), hint: `${t.eligible} of ${t.total} ${t.tier} drives open to you` },
        ])} />
        <p className="small muted" style={{ marginTop: 6 }}>
          {a.by_tier.map((t) => `${t.tier}: ${t.eligible}/${t.total}`).join(" · ")}
        </p>
      </Card>
      <Card title="You vs the batch" subtitle="Share of attempts you cleared, against everyone this season.">
        <div className="stack">
          {a.pass_rates.map((r) => (
            <div key={r.stage}>
              <h3 style={{ marginBottom: 6 }}>{r.stage}</h3>
              <HBars max={100} rows={[
                { label: "You", value: r.you ?? 0, note: r.you === null ? "no attempts yet" : `${r.you}%` },
                { label: "Batch", value: r.batch ?? 0, note: r.batch === null ? "—" : `${r.batch}%` },
              ]} />
            </div>
          ))}
        </div>
      </Card>
      <Card title="Your applications, week by week" subtitle="Last eight weeks." className="span-2">
        <LineChart points={a.weekly_applications.map((w) => ({ label: w.week, value: w.count }))} />
      </Card>
    </div>
  );
}

export function StudentHome() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["me", "dashboard"],
    queryFn: () => api.get<StudentDashboard>("/api/me/dashboard"),
    refetchInterval: 60_000,
  });
  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorBox error={error} />;
  const first = data.student.name.split(" ")[0];
  const opp = data.opportunity;
  const pl = data.placement;
  const lead = !pl.placed
    ? `You're eligible for ${plural(opp.eligible_now.length, "open drive")} right now.`
    : pl.season_over
      ? `You're placed at ${pl.companies.join(", ")}. Under institute policy your placement season is complete.`
      : `You're placed at ${pl.companies.join(", ")} (${pl.tier}). You have ${plural(pl.upgrade?.left ?? 0, "chance")} left at ${pl.upgrade?.threshold_lpa ?? 10}+ LPA drives.`;

  return (
    <>
      <section className="hero">
        <div>
          <h1>Hi {first}</h1>
          <p>{lead}</p>
          {data.unread > 0 && <div style={{ marginTop: 10 }}><Badge tone="info">{plural(data.unread, "new update")}</Badge></div>}
        </div>
        <div className="kpis">
          <div className="kpi"><div className="v">{data.student.branch}</div><div className="l">{data.student.roll_no}</div></div>
          <div className="kpi"><div className="v">{opp.eligible_now.length}</div><div className="l">open to you</div></div>
          <div className="kpi"><div className="v">{data.actions.length}</div><div className="l">need you</div></div>
          <div className="kpi"><div className="v">{pl.best_ctc_lpa ? `${pl.best_ctc_lpa} LPA` : "—"}</div><div className="l">{pl.placed ? `best offer · ${pl.tier}` : "not placed yet"}</div></div>
        </div>
      </section>

      {pl.upgrade && (
        <div className="callout signal" style={{ marginBottom: 16 }}>
          <strong>Upgrade chances: {pl.upgrade.left} of {pl.upgrade.total} left.</strong> Each application to a drive
          paying {pl.upgrade.threshold_lpa}+ LPA uses one. An offer at that level, or from a PSU, ends your season.
          <div className="row" style={{ gap: 6, marginTop: 8 }}>
            {Array.from({ length: pl.upgrade.total }, (_, i) => (
              <span key={i} className={`badge ${i < pl.upgrade!.used ? "" : "ok"}`}>{i < pl.upgrade!.used ? "Used" : "Available"}</span>
            ))}
          </div>
        </div>
      )}

      {data.live.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <DriveDay items={data.live} />
        </div>
      )}

      <div className="grid grid-2">
        <Card title="What needs you" subtitle="Ranked by what's due first.">
          {data.actions.length === 0 ? (
            <Empty title="You're all caught up">New drives you're eligible for will show up here.</Empty>
          ) : (
            data.actions.map((a, i) => {
              const urgent = a.due ? new Date(a.due).getTime() - Date.now() < 6 * 3600_000 : false;
              return (
                <div className="action" key={`${a.kind}-${i}`}>
                  <span className={`when ${urgent ? "urgent" : ""}`}>
                    {a.due ? (a.kind === "interview"
                      ? (dayLabel(a.due) === "Today" ? clock(a.due) : `${dayLabel(a.due)} ${clock(a.due)}`)
                      : relative(a.due)) : "To do"}
                  </span>
                  <div>
                    <strong>{a.title}</strong>
                    <div className="small muted">{a.detail}</div>
                  </div>
                  <Link className="btn small" to={a.link}>
                    Open
                  </Link>
                </div>
              );
            })
          )}
        </Card>

        <Card title="Where you're losing" subtitle={data.insight}>
          <Funnel stages={data.funnel} />
        </Card>

        <Card title="What the jobs you can get ask for"
          subtitle={`Across ${plural(data.skills.eligible_drives, "drive")} you're eligible for.`}>
          {data.skills.gaps.length === 0 ? (
            <p className="muted">Your profile covers the skills these drives ask for.</p>
          ) : (
            <div className="stack" style={{ gap: 8 }}>
              {data.skills.gaps.map((g) => (
                <div className="row between" key={g.skill}>
                  <span className="chip missing">{g.skill}</span>
                  <span className="small muted">{g.drives} of {g.of} drives ask for it; not on your profile</span>
                </div>
              ))}
            </div>
          )}
          {data.skills.strengths.length > 0 && (
            <p className="small muted" style={{ marginTop: 12 }}>
              Already strong: {data.skills.strengths.map((s) => s.skill).join(", ")}.
            </p>
          )}
        </Card>

        <Card title="Your opportunities" subtitle="What's open, what you could unlock and what an offer would cost.">
          <div className="stack">
            <div>
              <h3>Eligible now</h3>
              {opp.eligible_now.length === 0 ? (
                <p className="small muted">No open drive matches you today.</p>
              ) : (
                <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                  {opp.eligible_now.map((d) => (
                    <li key={d.id}>
                      <Link to={`/student/drives/${d.id}`}>{d.label}</Link> <span className="faint small">{d.tier}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            {opp.unlockable.map((u) => (
              <div className="callout warn" key={u.clear_backlogs}>
                Clear {plural(u.clear_backlogs, "backlog")} to unlock {plural(u.drives.length, "more drive")}:{" "}
                {u.drives.join(", ")}.
              </div>
            ))}
            {opp.lost_if_accept.map((l) => (
              <div className="callout" key={l.offer_id}>
                <strong>If you accept {l.company}</strong> ({l.tier}):{" "}
                {l.lost.length ? `you'd lose access to ${l.lost.join(", ")}.` : "you keep access to every drive you're eligible for."}{" "}
                <Link to="/student/offers">Decide</Link>
              </div>
            ))}
          </div>
        </Card>
      </div>
      <StandingCharts />
    </>
  );
}
