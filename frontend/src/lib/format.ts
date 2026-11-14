// Formatting helpers. All API times are UTC ISO strings; we display them in the viewer's local time.

export function parseTime(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function clock(iso: string | null | undefined): string {
  const d = parseTime(iso);
  return d ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—";
}

export function dateShort(iso: string | null | undefined): string {
  const d = parseTime(iso);
  return d ? d.toLocaleDateString([], { day: "numeric", month: "short" }) : "—";
}

export function dateTime(iso: string | null | undefined): string {
  const d = parseTime(iso);
  return d ? `${dateShort(iso)}, ${clock(iso)}` : "—";
}

/** "in 2 h", "5 min ago", "tomorrow". `now` is injectable for tests. */
export function relative(iso: string | null | undefined, now: Date = new Date()): string {
  const d = parseTime(iso);
  if (!d) return "";
  const diffMin = Math.round((d.getTime() - now.getTime()) / 60000);
  const abs = Math.abs(diffMin);
  const future = diffMin >= 0;
  if (abs < 1) return "now";
  if (abs < 60) return future ? `in ${abs} min` : `${abs} min ago`;
  const hours = Math.round(abs / 60);
  if (hours < 24) return future ? `in ${hours} h` : `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days === 1) return future ? "tomorrow" : "yesterday";
  return future ? `in ${days} days` : `${days} days ago`;
}

export function lpa(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${Number.isInteger(value) ? value : value.toFixed(1)} LPA`;
}

export function pct(part: number, whole: number): string {
  if (!whole) return "0%";
  return `${Math.round((100 * part) / whole)}%`;
}

export function ruleText(field: string, op: string, value: unknown): string {
  const labels: Record<string, string> = {
    cgpa: "CGPA",
    backlogs: "Active backlogs",
    branch: "Branch",
    programme: "Programme",
    grad_year: "Graduation year",
  };
  const label = labels[field] ?? field;
  const v = Array.isArray(value) ? value.join(", ") : String(value);
  if (op === ">=") return `${label} at least ${v}`;
  if (op === "<=") return field === "backlogs" && Number(value) === 0 ? "No active backlogs" : `${label} at most ${v}`;
  return `${label}: ${v}`;
}

export function toLocalInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function fromLocalInput(value: string): string {
  return new Date(value).toISOString();
}

export function plural(n: number, word: string, many?: string): string {
  return `${n} ${n === 1 ? word : (many ?? `${word}s`)}`;
}
