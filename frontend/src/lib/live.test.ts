import { describe, expect, it } from "vitest";
import { liveUrl } from "./live";

describe("liveUrl", () => {
  it("uses wss on https and encodes link tokens", () => {
    expect(liveUrl({ protocol: "https:", host: "driveops.example" })).toBe("wss://driveops.example/ws");
    expect(liveUrl({ protocol: "http:", host: "localhost:5173" }, "a.b+c")).toBe("ws://localhost:5173/ws?token=a.b%2Bc");
  });
});
