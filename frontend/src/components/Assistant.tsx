import { Bot, Copy, Send, Sparkles, Square, Trash2, X } from "lucide-react";
import { Fragment, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { ApiError } from "../api/client";
import { streamChat, type ChatMessage } from "../lib/assistant";
import { useCopyLink } from "../lib/clipboard";

// ---------------------------------------------------------------- tiny, safe markdown (no HTML injection)
function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    out.push(tok.startsWith("**")
      ? <strong key={`${key}-${i++}`}>{tok.slice(2, -2)}</strong>
      : <code key={`${key}-${i++}`}>{tok.slice(1, -1)}</code>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** Paragraphs, bullet and numbered lists, **bold** and `code`. Everything else is shown as text. */
export function MarkdownLite({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  const lines = text.replace(/\r/g, "").split("\n");
  let list: { ordered: boolean; items: string[] } | null = null;
  let para: string[] = [];
  const flushPara = () => {
    if (para.length) blocks.push(<p key={`p${blocks.length}`}>{inline(para.join(" "), `p${blocks.length}`)}</p>);
    para = [];
  };
  const flushList = () => {
    if (!list) return;
    const items = list.items.map((it, j) => <li key={j}>{inline(it, `l${blocks.length}-${j}`)}</li>);
    blocks.push(list.ordered ? <ol key={`o${blocks.length}`}>{items}</ol> : <ul key={`u${blocks.length}`}>{items}</ul>);
    list = null;
  };
  for (const raw of lines) {
    const line = raw.trim();
    const bullet = /^[-*•]\s+(.*)$/.exec(line);
    const num = /^\d+[.)]\s+(.*)$/.exec(line);
    if (bullet || num) {
      flushPara();
      const ordered = Boolean(num);
      if (!list || list.ordered !== ordered) {
        flushList();
        list = { ordered, items: [] };
      }
      list.items.push((bullet ?? num)![1]);
    } else if (!line) {
      flushPara();
      flushList();
    } else {
      flushList();
      para.push(line.replace(/^#+\s*/, ""));
    }
  }
  flushPara();
  flushList();
  return <div className="md">{blocks.map((b, i) => <Fragment key={i}>{b}</Fragment>)}</div>;
}

// ---------------------------------------------------------------- chat
interface Turn extends ChatMessage {
  provider?: string;
  error?: boolean;
}

function load(key: string): Turn[] {
  try {
    const raw = sessionStorage.getItem(key);
    return raw ? (JSON.parse(raw) as Turn[]) : [];
  } catch {
    return [];
  }
}

function save(key: string, turns: Turn[]) {
  try {
    sessionStorage.setItem(key, JSON.stringify(turns.slice(-30)));
  } catch {
    // storage blocked: the chat still works for this page view
  }
}

export function AssistantChat({
  endpoint,
  suggestions,
  intro,
  storageKey,
  compact,
}: {
  endpoint: string;
  suggestions: string[];
  intro: string;
  storageKey: string;
  compact?: boolean;
}) {
  const [turns, setTurns] = useState<Turn[]>(() => load(storageKey));
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const end = useRef<HTMLDivElement | null>(null);
  const copy = useCopyLink();

  useEffect(() => {
    save(storageKey, turns);
  }, [storageKey, turns]);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [turns]);
  useEffect(() => () => abort.current?.abort(), []);

  const send = async (q: string) => {
    const question = q.trim();
    if (!question || busy) return;
    const asked: ChatMessage = { role: "user", content: question };
    const history: ChatMessage[] = [...turns.filter((t) => !t.error), asked]
      .map(({ role, content }): ChatMessage => ({ role, content }))
      .slice(-12);
    setTurns((t) => [...t, { role: "user", content: question }, { role: "assistant", content: "" }]);
    setText("");
    setBusy(true);
    const ctl = new AbortController();
    abort.current = ctl;
    const patch = (fn: (last: Turn) => Turn) => setTurns((t) => [...t.slice(0, -1), fn(t[t.length - 1])]);
    try {
      await streamChat(endpoint, history, (e) => {
        if (e.provider) patch((last) => ({ ...last, provider: e.provider }));
        if (e.delta) patch((last) => ({ ...last, content: last.content + e.delta }));
      }, ctl.signal);
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        patch((last) => ({ ...last, content: last.content || "(stopped)" }));
      } else {
        const msg = err instanceof ApiError ? err.message : "Couldn't reach the assistant. Check your connection.";
        patch((last) => ({ ...last, content: msg, error: true }));
      }
    } finally {
      setBusy(false);
      abort.current = null;
    }
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send(text);
    }
  };

  return (
    <div className={`chat ${compact ? "compact" : ""}`}>
      <div className="chat-log" aria-live="polite">
        {turns.length === 0 && (
          <div className="chat-empty">
            <Bot size={28} aria-hidden />
            <p>{intro}</p>
            <div className="chat-suggest">
              {suggestions.map((s) => (
                <button key={s} className="chip" onClick={() => void send(s)}>{s}</button>
              ))}
            </div>
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className={`bubble ${t.role} ${t.error ? "error" : ""}`}>
            {t.role === "assistant" ? (
              t.content ? <MarkdownLite text={t.content} /> : <span className="typing" aria-label="Thinking"><i /><i /><i /></span>
            ) : t.content}
            {t.role === "assistant" && t.content && !t.error && !(busy && i === turns.length - 1) && (
              <div className="bubble-meta">
                <span>{t.provider === "policy-engine" ? "Policy engine" : t.provider === "summary" ? "Summary" : t.provider ? `AI · ${t.provider}` : ""}</span>
                <button className="icon-btn" title="Copy answer" aria-label="Copy answer" onClick={() => copy(t.content, "Answer")}><Copy size={13} /></button>
              </div>
            )}
          </div>
        ))}
        <div ref={end} />
      </div>
      <form className="chat-input" onSubmit={(e) => { e.preventDefault(); void send(text); }}>
        <textarea value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} rows={1} maxLength={1500}
          placeholder="Ask a question…" aria-label="Message" />
        {busy ? (
          <button type="button" className="btn small" onClick={() => abort.current?.abort()} title="Stop"><Square size={14} /> Stop</button>
        ) : (
          <button type="submit" className="btn primary small" disabled={!text.trim()} title="Send (Enter)"><Send size={14} /> Send</button>
        )}
      </form>
      <div className="chat-foot">
        <span>AI answers can be wrong. Check deadlines and offers on their pages.</span>
        {turns.length > 0 && !busy && (
          <button className="icon-btn" onClick={() => setTurns([])} title="Clear conversation" aria-label="Clear conversation"><Trash2 size={13} /> Clear</button>
        )}
      </div>
    </div>
  );
}

/** Floating launcher + panel, shown on every page of a role's app. */
export function AssistantLauncher(props: { endpoint: string; suggestions: string[]; intro: string; storageKey: string }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <>
      {open && (
        <section className="assist-panel" role="dialog" aria-label="HireFlo Assistant">
          <header>
            <strong><Sparkles size={16} aria-hidden /> HireFlo Assistant</strong>
            <button className="icon-btn" onClick={() => setOpen(false)} aria-label="Close assistant"><X size={16} /></button>
          </header>
          <AssistantChat {...props} compact />
        </section>
      )}
      <button className={`assist-fab ${open ? "open" : ""}`} onClick={() => setOpen((o) => !o)} title="Ask HireFlo (Ctrl+J)"
        aria-expanded={open}>
        {open ? <X size={20} /> : <Sparkles size={20} />}
        {!open && <span>Ask HireFlo</span>}
      </button>
    </>
  );
}

export const STUDENT_SUGGESTIONS = [
  "Which open drives am I eligible for?",
  "When is my next interview?",
  "Why am I not eligible for some drives?",
  "If I accept my offer, which drives can I still sit for?",
  "How many offers can I accept?",
];

export const CELL_SUGGESTIONS = [
  "How is the season going so far?",
  "Which branches are behind on placements?",
  "Which drives close this week and how many have applied?",
  "What are the main early-warning reasons?",
  "Which drives still have rules waiting for approval?",
];
