import { describe, expect, it } from "vitest";
import { parseSse } from "./assistant";

describe("parseSse", () => {
  it("returns complete events and keeps the unfinished tail", () => {
    const { events, rest } = parseSse('data: {"provider":"groq:x"}\n\ndata: {"delta":"Hel"}\n\ndata: {"delta":"l');
    expect(events).toEqual([{ provider: "groq:x" }, { delta: "Hel" }]);
    expect(rest).toBe('data: {"delta":"l');
    const next = parseSse(rest + 'o"}\n\ndata: {"done":true}\n\n');
    expect(next.events).toEqual([{ delta: "lo" }, { done: true }]);
    expect(next.rest).toBe("");
  });

  it("skips malformed events", () => {
    expect(parseSse("data: {oops\n\ndata: {\"delta\":\"x\"}\n\n").events).toEqual([{ delta: "x" }]);
  });
});
