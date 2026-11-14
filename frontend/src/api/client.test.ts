import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, errorMessage, request } from "./client";

function mockFetch(status: number, body: unknown, contentType = "application/json") {
  const fn = vi.fn().mockImplementation(async () =>
    new Response(typeof body === "string" ? body : JSON.stringify(body), { status, headers: { "content-type": contentType } }),
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("request", () => {
  it("sends the CSRF header on writes but not on reads", async () => {
    const fetch = mockFetch(200, { ok: true });
    await request("GET", "/api/x");
    expect(fetch.mock.calls[0][1].headers["X-DriveOps-CSRF"]).toBeUndefined();
    await request("POST", "/api/x", { body: { a: 1 } });
    const init = fetch.mock.calls[1][1];
    expect(init.headers["X-DriveOps-CSRF"]).toBe("1");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(init.body).toBe('{"a":1}');
  });

  it("passes magic-link tokens as a header, never in the URL", async () => {
    const fetch = mockFetch(200, {});
    await request("GET", "/api/r/overview", { token: "abc.def" });
    expect(fetch.mock.calls[0][0]).toBe("/api/r/overview");
    expect(fetch.mock.calls[0][1].headers["X-Access-Token"]).toBe("abc.def");
  });

  it("turns API errors into readable messages", async () => {
    mockFetch(400, { detail: "You're not eligible: CGPA 6.5 is below the required 7" });
    await expect(request("POST", "/api/me/drives/1/apply")).rejects.toThrow(/not eligible/);
    mockFetch(401, { detail: "Sign in to continue." });
    await expect(request("GET", "/api/me/dashboard")).rejects.toBeInstanceOf(ApiError);
  });

  it("returns undefined for 204", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    await expect(request("DELETE", "/api/x")).resolves.toBeUndefined();
  });
});

describe("errorMessage", () => {
  it("flattens FastAPI validation errors", () => {
    const detail = [{ loc: ["body", "cgpa"], msg: "Input should be less than or equal to 10" }];
    expect(errorMessage(detail, "x")).toBe("cgpa: Input should be less than or equal to 10");
    expect(errorMessage(undefined, "fallback")).toBe("fallback");
  });
});
