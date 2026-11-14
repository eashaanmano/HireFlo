import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

export type LiveState = "connecting" | "live" | "offline";

export function liveUrl(location: Pick<Location, "protocol" | "host">, token?: string | null): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const q = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${proto}//${location.host}/ws${q}`;
}

/**
 * Subscribes to the live event stream. Every notice is only a hint ("something changed"); we refetch the
 * active queries, debounced, so all three actors stay in sync without the server pushing personal data.
 */
export function useLive(enabled: boolean, token?: string | null): LiveState {
  const qc = useQueryClient();
  const [state, setState] = useState<LiveState>("connecting");
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!enabled) return undefined;
    let ws: WebSocket | null = null;
    let retry = 0;
    let closed = false;
    let reconnect: number | undefined;

    const refresh = () => {
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => {
        void qc.invalidateQueries({ refetchType: "active" });
      }, 250);
    };

    const connect = () => {
      setState("connecting");
      ws = new WebSocket(liveUrl(window.location, token));
      ws.onopen = () => {
        retry = 0;
        setState("live");
        refresh(); // catch up on anything missed while disconnected
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(String(ev.data)) as { type?: string };
          if (msg.type && msg.type !== "ping") refresh();
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => {
        setState("offline");
        if (closed) return;
        retry = Math.min(retry + 1, 6);
        reconnect = window.setTimeout(connect, 500 * 2 ** retry);
      };
    };
    connect();
    return () => {
      closed = true;
      window.clearTimeout(reconnect);
      window.clearTimeout(timer.current);
      ws?.close();
    };
  }, [enabled, token, qc]);

  return state;
}
