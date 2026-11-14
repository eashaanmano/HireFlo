import { afterEach, describe, expect, it, vi } from "vitest";
import { copyText } from "./clipboard";

describe("copyText", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("uses the Clipboard API in a secure context", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("isSecureContext", true);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    expect(await copyText("https://x/r/abc")).toBe(true);
    expect(writeText).toHaveBeenCalledWith("https://x/r/abc");
  });

  it("falls back to execCommand on plain http", async () => {
    vi.stubGlobal("isSecureContext", false);
    const exec = vi.fn().mockReturnValue(true);
    Object.defineProperty(document, "execCommand", { value: exec, configurable: true });
    expect(await copyText("http://192.168.1.5/p/xyz")).toBe(true);
    expect(exec).toHaveBeenCalledWith("copy");
    expect(document.querySelector("textarea")).toBeNull();
  });

  it("reports failure when nothing can copy", async () => {
    vi.stubGlobal("isSecureContext", false);
    Object.defineProperty(document, "execCommand", { value: () => false, configurable: true });
    expect(await copyText("x")).toBe(false);
  });
});
