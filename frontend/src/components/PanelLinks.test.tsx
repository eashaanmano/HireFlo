import { describe, expect, it } from "vitest";
import { allLinksText } from "./PanelLinks";

describe("allLinksText", () => {
  it("puts every panel on its own line under the heading", () => {
    const t = allLinksText("UST Global: Developer", [
      { panel_id: 1, name: "Panel A", round: "Technical interview", room: "Seminar Hall 1", url: "https://h/p/a" },
      { panel_id: 2, name: "HR", round: "HR interview", room: null, url: "https://h/p/b" },
    ]);
    expect(t.split("\n")).toEqual([
      "UST Global: Developer",
      "Panel A (Technical interview, Seminar Hall 1): https://h/p/a",
      "HR (HR interview): https://h/p/b",
    ]);
  });
});
