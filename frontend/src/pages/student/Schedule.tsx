import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, download } from "../../api/client";
import type { Slot } from "../../api/types";
import { MonthCalendar, type CalEvent } from "../../components/viz";
import { Badge, Button, Card, Empty, ErrorBox, Loading, PageHead, useToast } from "../../components/ui";
import { clock, dateShort } from "../../lib/format";

const STATUS: Record<string, { label: string; tone?: "ok" | "warn" | "bad" | "info" }> = {
  scheduled: { label: "Upcoming", tone: "info" },
  in_progress: { label: "In progress", tone: "warn" },
  done: { label: "Done", tone: "ok" },
  no_show: { label: "Marked absent", tone: "bad" },
  cancelled: { label: "Cancelled" },
};

export function StudentSchedule() {
  const toast = useToast();
  const [view, setView] = useState<"calendar" | "list">("calendar");
  const { data, isLoading, error } = useQuery({ queryKey: ["me", "schedule"], queryFn: () => api.get<Slot[]>("/api/me/schedule") });
  const cal = useQuery({ queryKey: ["me", "calendar"], queryFn: () => api.get<CalEvent[]>("/api/me/calendar") });
  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorBox error={error} />;

  const clashes = new Set<number>();
  const active = data.filter((s) => s.status === "scheduled");
  for (const a of active) {
    for (const b of active) {
      if (a.id !== b.id && a.start_at < b.end_at && b.start_at < a.end_at) clashes.add(a.id);
    }
  }
  const days = new Map<string, Slot[]>();
  for (const s of data) {
    const key = dateShort(s.start_at);
    days.set(key, [...(days.get(key) ?? []), s]);
  }

  return (
    <>
      <PageHead
        title="Your timeline"
        lead="Interviews, application deadlines, drive days and offer deadlines, in one place."
        right={
          <div className="row">
          <div className="seg" role="tablist" aria-label="View">
            <button className={view === "calendar" ? "on" : ""} onClick={() => setView("calendar")}>Calendar</button>
            <button className={view === "list" ? "on" : ""} onClick={() => setView("list")}>Interviews</button>
          </div>
          <Button onClick={() => download("/api/me/schedule.ics", "driveops.ics").catch(() => toast("Download failed", true))}>
            Add to calendar
          </Button>
          </div>
        }
      />
      {view === "calendar" ? (
        <Card>{cal.data ? <MonthCalendar events={cal.data} /> : <Loading />}</Card>
      ) : data.length === 0 ? (
        <Card><Empty title="No interviews scheduled">When a drive schedules you, the slot appears here with its room and panel.</Empty></Card>
      ) : (
        [...days.entries()].map(([day, slots]) => (
          <Card key={day} title={day}>
            <div className="table-wrap">
              <table>
                <tbody>
                  {slots.map((s) => {
                    const late = s.projected_start && s.lateness_min > 2;
                    return (
                      <tr key={s.id}>
                        <td className="ops num" style={{ width: 90, fontSize: 17 }}>{clock(s.start_at)}</td>
                        <td>
                          <strong>{s.round}</strong>
                          <div className="small muted">{s.panel}, {s.room}</div>
                          {clashes.has(s.id) && <div className="small" style={{ color: "var(--red)" }}>Clashes with another slot. Tell the placement cell.</div>}
                        </td>
                        <td>
                          <Badge tone={STATUS[s.status]?.tone}>{STATUS[s.status]?.label ?? s.status}</Badge>
                          {late && <div className="small" style={{ color: "var(--amber)" }}>Now about {clock(s.projected_start)}</div>}
                        </td>
                        <td className="small">{s.result ? (s.result === "pass" ? "Cleared" : "Not cleared") : s.checked_in ? "Checked in" : ""}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        ))
      )}
    </>
  );
}
