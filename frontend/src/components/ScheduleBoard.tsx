// Per-round Gantt of interview slots, shared by the recruiter and placement-cell views.
import type { Slot } from "../api/types";
import { dateShort, plural } from "../lib/format";
import { Empty } from "./ui";
import { Timeline, type TimelineItem } from "./viz";

export function slotsToTimeline(slots: Slot[]): TimelineItem[] {
  return slots.map((s) => {
    const start = s.projected_start ?? s.start_at;
    const dur = Date.parse(s.end_at) - Date.parse(s.start_at);
    return {
      id: s.id, lane: `${s.panel} · ${s.room}`, start, end: new Date(Date.parse(start) + dur).toISOString(),
      label: s.student, status: s.status, late: s.lateness_min,
    };
  });
}

function localDay(iso: string): string {
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** Slots grouped by local calendar day, so a drive over several days isn't drawn with the nights in between. */
export function splitByDay(slots: Slot[]): [string, Slot[]][] {
  const m = new Map<string, Slot[]>();
  for (const s of [...slots].sort((a, b) => Date.parse(a.start_at) - Date.parse(b.start_at))) {
    const k = localDay(s.projected_start ?? s.start_at);
    m.set(k, [...(m.get(k) ?? []), s]);
  }
  return [...m.entries()];
}

/** Earliest start and latest end as minutes after local midnight, across all days. */
function clockRange(slots: Slot[]): [number, number] {
  const mins = (iso: string) => { const d = new Date(iso); return d.getHours() * 60 + d.getMinutes(); };
  let lo = 24 * 60, hi = 0;
  for (const s of slots) {
    const start = s.projected_start ?? s.start_at;
    const end = Date.parse(start) + Date.parse(s.end_at) - Date.parse(s.start_at);
    lo = Math.min(lo, mins(start));
    hi = Math.max(hi, mins(new Date(end).toISOString()) || 24 * 60);
  }
  return [lo, Math.max(hi, lo + 30)];
}

export function ScheduleBoard({ slots }: { slots: Slot[] }) {
  const rounds = [...new Set(slots.map((s) => s.round))];
  if (!slots.length) return <Empty title="No interviews scheduled yet">The placement cell schedules interview rounds once shortlists are in.</Empty>;
  const now = new Date().toISOString();
  return (
    <div className="stack">
      {rounds.map((r, ri) => {
        const rs = slots.filter((s) => s.round === r);
        const days = splitByDay(rs);
        const [lo, hi] = clockRange(rs);
        const last = ri === rounds.length - 1;
        return (
          <div key={r}>
            <h3 style={{ marginBottom: 8 }}>{r} <span className="small faint">· {plural(rs.length, "slot")}{days.length > 1 ? ` over ${days.length} days` : ""}</span></h3>
            {days.length === 1 ? <Timeline items={slotsToTimeline(rs)} nowIso={now} legend={last} /> : days.map(([key, ds], di) => {
              const midnight = new Date(ds[0].projected_start ?? ds[0].start_at);
              midnight.setHours(0, 0, 0, 0);
              const range: [number, number] = [midnight.getTime() + lo * 60_000, midnight.getTime() + hi * 60_000];
              return (
                <div key={key} style={{ marginBottom: 10 }}>
                  <div className="small muted" style={{ margin: "4px 0" }}>{dateShort(ds[0].start_at)} · {plural(ds.length, "slot")}</div>
                  <Timeline items={slotsToTimeline(ds)} nowIso={now} range={range} legend={last && di === days.length - 1} />
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}
