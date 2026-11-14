import { describe, expect, it } from "vitest";
import { lpa, pct, plural, relative, ruleText } from "./format";

describe("relative", () => {
  const now = new Date("2026-10-29T10:00:00Z");
  it("handles minutes, hours and days in both directions", () => {
    expect(relative("2026-10-29T10:00:20Z", now)).toBe("now");
    expect(relative("2026-10-29T10:25:00Z", now)).toBe("in 25 min");
    expect(relative("2026-10-29T09:30:00Z", now)).toBe("30 min ago");
    expect(relative("2026-10-29T13:00:00Z", now)).toBe("in 3 h");
    expect(relative("2026-10-30T10:00:00Z", now)).toBe("tomorrow");
    expect(relative("2026-10-25T10:00:00Z", now)).toBe("4 days ago");
  });
  it("returns an empty string for missing values", () => {
    expect(relative(null, now)).toBe("");
    expect(relative("not a date", now)).toBe("");
  });
});

describe("ruleText", () => {
  it("phrases rules the way a student reads them", () => {
    expect(ruleText("cgpa", ">=", 7.5)).toBe("CGPA at least 7.5");
    expect(ruleText("backlogs", "<=", 0)).toBe("No active backlogs");
    expect(ruleText("backlogs", "<=", 2)).toBe("Active backlogs at most 2");
    expect(ruleText("branch", "in", ["CSE", "ECE"])).toBe("Branch: CSE, ECE");
  });
});

describe("small helpers", () => {
  it("formats money, percentages and plurals", () => {
    expect(lpa(24)).toBe("24 LPA");
    expect(lpa(7.5)).toBe("7.5 LPA");
    expect(lpa(null)).toBe("—");
    expect(pct(1, 3)).toBe("33%");
    expect(pct(1, 0)).toBe("0%");
    expect(plural(1, "drive")).toBe("1 drive");
    expect(plural(3, "drive")).toBe("3 drives");
  });
});
