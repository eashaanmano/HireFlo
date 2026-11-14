import { describe, expect, it } from "vitest";
import type { Slot } from "../api/types";
import { splitByDay } from "./ScheduleBoard";

const slot = (id: number, start: string): Slot =>
  ({ id, start_at: start, end_at: start, projected_start: null, round: "Tech", panel: "A", room: "R1", student: `S${id}`,
     status: "scheduled", lateness_min: 0 }) as unknown as Slot;

describe("splitByDay", () => {
  it("groups a multi-day round by local date, in time order", () => {
    const d1 = new Date(2026, 10, 16, 9, 0).toISOString();
    const d1b = new Date(2026, 10, 16, 17, 0).toISOString();
    const d2 = new Date(2026, 10, 17, 9, 0).toISOString();
    const days = splitByDay([slot(3, d2), slot(1, d1), slot(2, d1b)]);
    expect(days.map(([, s]) => s.map((x) => x.id))).toEqual([[1, 2], [3]]);
  });
});
