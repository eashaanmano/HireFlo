// Thin, typed fetch wrapper. Cookie sessions carry a CSRF header on writes; magic links send their token.

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface RequestOptions {
  body?: unknown;
  form?: FormData;
  token?: string | null;
  signal?: AbortSignal;
}

export function errorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    // FastAPI validation errors: [{loc: [...], msg: "..."}]
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => {
        const field = Array.isArray(d.loc) ? d.loc.filter((x) => x !== "body").join(".") : "";
        return field ? `${field}: ${d.msg ?? "invalid"}` : (d.msg ?? "invalid");
      })
      .join("; ");
  }
  return fallback;
}

export async function request<T>(method: Method, path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== "GET") headers["X-DriveOps-CSRF"] = "1";
  if (opts.token) headers["X-Access-Token"] = opts.token;
  let body: BodyInit | undefined;
  if (opts.form) {
    body = opts.form;
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, { method, headers, body, credentials: "same-origin", signal: opts.signal });
  if (res.status === 204) return undefined as T;
  const type = res.headers.get("content-type") ?? "";
  const data: unknown = type.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = typeof data === "object" && data !== null ? (data as { detail?: unknown }).detail : data;
    throw new ApiError(res.status, errorMessage(detail, `Request failed (${res.status})`));
  }
  return data as T;
}

export const api = {
  get: <T>(path: string, token?: string | null) => request<T>("GET", path, { token }),
  post: <T>(path: string, body?: unknown, token?: string | null) => request<T>("POST", path, { body, token }),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, { body }),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, { body }),
  del: <T>(path: string) => request<T>("DELETE", path),
  upload: <T>(path: string, form: FormData) => request<T>("POST", path, { form }),
};

/** Download a file from an authenticated endpoint (cookie or link token) and save it. */
export async function download(path: string, filename: string, token?: string | null): Promise<void> {
  const headers: Record<string, string> = token ? { "X-Access-Token": token } : {};
  const res = await fetch(path, { headers, credentials: "same-origin" });
  if (!res.ok) throw new ApiError(res.status, `Download failed (${res.status})`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
