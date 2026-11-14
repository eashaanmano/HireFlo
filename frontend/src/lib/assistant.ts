// Streaming client for the HireFlo Assistant (server-sent events over a POST request).
import { ApiError, errorMessage } from "../api/client";

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface StreamEvent {
  provider?: string;
  delta?: string;
  done?: boolean;
}

/** Split a server-sent-events buffer into complete events; the incomplete tail is returned for the next chunk. */
export function parseSse(buffer: string): { events: StreamEvent[]; rest: string } {
  const events: StreamEvent[] = [];
  const blocks = buffer.split(/\r?\n\r?\n/);
  const rest = blocks.pop() ?? "";
  for (const block of blocks) {
    const data = block
      .split(/\r?\n/)
      .filter((l) => l.startsWith("data:"))
      .map((l) => l.slice(5).trimStart())
      .join("\n");
    if (!data) continue;
    try {
      events.push(JSON.parse(data) as StreamEvent);
    } catch {
      // ignore a malformed event rather than breaking the whole answer
    }
  }
  return { events, rest };
}

/** POST the conversation and call `onEvent` for every streamed event. Resolves when the stream ends. */
export async function streamChat(
  path: string,
  messages: ChatMessage[],
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-DriveOps-CSRF": "1" },
    body: JSON.stringify({ messages }),
    credentials: "same-origin",
    signal,
  });
  if (!res.ok) {
    let detail: unknown = `Request failed (${res.status})`;
    try {
      detail = ((await res.json()) as { detail?: unknown }).detail;
    } catch {
      // not JSON
    }
    throw new ApiError(res.status, errorMessage(detail, `Request failed (${res.status})`));
  }
  if (!res.body) {
    // very old browsers: read it all at once
    const { events } = parseSse((await res.text()) + "\n\n");
    events.forEach(onEvent);
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parsed = parseSse(buffer);
    buffer = parsed.rest;
    parsed.events.forEach(onEvent);
  }
  if (buffer.trim()) parseSse(buffer + "\n\n").events.forEach(onEvent);
}
