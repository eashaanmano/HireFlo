import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { Slot } from "../api/types";
import { slotsToTimeline } from "./ScheduleBoard";
import { BarChart, CutoffStrip, MonthCalendar, Timeline } from "./viz";

const slot = (over: Partial<Slot>): Slot => ({
  id: 1, round: "Technical", round_id: 1, panel: "Panel A", panel_id: 1, room: "CSED 201", student: "Eashaan",
  student_id: 1, roll_no: "M250012CS", status: "scheduled", start_at: "2026-10-29T04:00:00Z",
  end_at: "2026-10-29T04:30:00Z", projected_start: null, lateness_min: 0, checked_in: false, result: null, ...over,
});

describe("slotsToTimeline", () => {
  it("shifts a late slot to its projected start and keeps its length", () => {
    const [item] = slotsToTimeline([slot({ projected_start: "2026-10-29T04:20:00Z", lateness_min: 20 })]);
    expect(item.start).toBe("2026-10-29T04:20:00Z");
    expect(item.end).toBe("2026-10-29T04:50:00.000Z");
    expect(item.lane).toBe("Panel A · CSED 201");
    expect(item.late).toBe(20);
  });
});

describe("viz kit", () => {
  it("renders a bar per datum with values", () => {
    const { container } = render(<BarChart data={[{ label: "CSE", value: 4 }, { label: "ECE", value: 2 }]} />);
    expect(container.querySelectorAll("path").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("CSE")).toBeTruthy();
  });

  it("flags cutoffs the student misses", () => {
    render(
      <MemoryRouter>
        <CutoffStrip mine={8.1} rows={[
          { label: "NVIDIA", value: 8.5, color: "red", ok: false },
          { label: "TCS", value: 6, color: "blue", ok: true },
        ]} />
      </MemoryRouter>,
    );
    expect(screen.getByText("NVIDIA")).toBeTruthy();
    expect(screen.getByText("You 8.10")).toBeTruthy();
  });

  it("draws one lane per panel", () => {
    render(<Timeline items={slotsToTimeline([slot({}), slot({ id: 2, panel: "Panel B" })])} />);
    expect(screen.getByText("Panel A · CSED 201")).toBeTruthy();
    expect(screen.getByText("Panel B · CSED 201")).toBeTruthy();
  });

  it("shows events on the calendar day", () => {
    const today = new Date();
    today.setHours(10, 0, 0, 0);
    render(
      <MemoryRouter>
        <MonthCalendar events={[{ kind: "interview", start: today.toISOString(), end: null, title: "AMD: Technical",
          detail: "Panel A", status: "scheduled", link: "/student/schedule" }]} />
      </MemoryRouter>,
    );
    expect(screen.getAllByText(/AMD/).length).toBeGreaterThan(0);
  });
});
