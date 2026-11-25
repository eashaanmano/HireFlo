import { describe, expect, it } from "vitest";
import { blankJnf, criteriaOf, draftFrom, jnfPayload } from "./jnf";

describe("job notification form helpers", () => {
  it("turns empty numbers into null and keeps zeros", () => {
    const d = { ...blankJnf(), role: "Engineer", ctc_lpa: "24", cgpa_min: "", max_backlogs: "0", base_lpa: " " };
    const p = jnfPayload(d);
    expect(p.ctc_lpa).toBe(24);
    expect(p.cgpa_min).toBeNull();
    expect(p.max_backlogs).toBe(0);
    expect(p.base_lpa).toBeNull();
    expect(p.location).toBeNull();
  });

  it("upper-cases branches and drops duplicates", () => {
    const p = jnfPayload({ ...blankJnf(), branches: ["cse", "CSE", " ece "] });
    expect(p.branches).toEqual(["CSE", "ECE"]);
  });

  it("keeps rounds in order with numeric durations", () => {
    const p = jnfPayload({
      ...blankJnf(),
      rounds: [
        { name: " Online test ", kind: "test", duration_min: "60" },
        { name: "Technical interview", kind: "interview", duration_min: "30" },
      ],
    });
    expect(p.rounds).toEqual([
      { name: "Online test", kind: "test", duration_min: 60 },
      { name: "Technical interview", kind: "interview", duration_min: 30 },
    ]);
  });

  it("round-trips a saved form back into the editor", () => {
    const d = { ...blankJnf(), role: "Engineer", ctc_lpa: "12.5", bond_months: "6", preferred_date: "2026-12-10" };
    expect(jnfPayload(draftFrom(jnfPayload(d)))).toEqual(jnfPayload(d));
    expect(draftFrom(null)).toEqual(blankJnf());
  });

  it("asks for a count only once a CTC is given", () => {
    expect(criteriaOf(blankJnf())).toBeNull();
    expect(criteriaOf({ ...blankJnf(), ctc_lpa: "24", branches: ["cse"], cgpa_min: "7" })).toEqual({
      ctc_lpa: 24, branches: ["CSE"], programmes: [], cgpa_min: 7, max_backlogs: null,
    });
  });
});
