// Small, dependency-free chart kit: bar, line, cutoff strip, timeline (Gantt) and month calendar.
// Every mark has a hover tooltip; identity is never carried by colour alone (labels and legends).
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";

export const TIER_COLOR: Record<string, string> = {
  Regular: "var(--tier-regular)",
  Dream: "var(--tier-dream)",
  "Super Dream": "var(--tier-super)",
  PSU: "var(--tier-psu)",
};

export function tierColor(name: string): string {
  return TIER_COLOR[name] ?? "var(--signal)";
}

// ---------------------------------------------------------------- responsive width
/** Measure the chart's container so the SVG is drawn at its real width and text stays a constant size. */
function useWidth(fallback = 600) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [w, setW] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const width = Math.round(entries[0]?.contentRect.width ?? 0);
      if (width > 0) setW(Math.max(280, width));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

// ---------------------------------------------------------------- tooltip
interface Tip {
  x: number;
  y: number;
  content: ReactNode;
}

function useTip() {
  const [tip, setTip] = useState<Tip | null>(null);
  const place = (el: Element, x: number, y: number, content: ReactNode) => {
    const host = el.closest(".viz") as HTMLElement | null;
    const r = host?.getBoundingClientRect();
    setTip({ x: x - (r?.left ?? 0), y: y - (r?.top ?? 0), content });
  };
  const show = (e: MouseEvent, content: ReactNode) => place(e.currentTarget as Element, e.clientX, e.clientY, content);
  /** Keyboard focus: anchor the tooltip above the focused mark. */
  const showAt = (el: Element, content: ReactNode) => {
    const b = el.getBoundingClientRect();
    place(el, b.left + b.width / 2, b.top, content);
  };
  const node = tip ? (
    <div className="viz-tip" style={{ left: tip.x, top: tip.y }} role="status">
      {tip.content}
    </div>
  ) : null;
  return { show, showAt, hide: () => setTip(null), node };
}

export function Legend({ items }: { items: { label: string; color: string }[] }) {
  return (
    <div className="viz-legend">
      {items.map((i) => (
        <span key={i.label}>
          <i style={{ background: i.color }} aria-hidden />
          {i.label}
        </span>
      ))}
    </div>
  );
}

/** Clickable legend: each series can be hidden or shown. */
function ToggleLegend({ items, off, onToggle }: { items: { label: string; color: string }[]; off: Set<string>; onToggle: (l: string) => void }) {
  return (
    <div className="viz-legend">
      {items.map((i) => (
        <button key={i.label} className={off.has(i.label) ? "off" : ""} onClick={() => onToggle(i.label)} aria-pressed={!off.has(i.label)}>
          <i style={{ background: i.color }} aria-hidden />
          {i.label}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- bar chart (vertical)
export interface BarDatum {
  label: string;
  value: number;
  color?: string;
  hint?: string;
}

export function BarChart({
  data,
  height = 180,
  unit = "",
  labelEvery = 1,
  showValues = true,
  onSelect,
  selected,
  total,
}: {
  data: BarDatum[];
  height?: number;
  unit?: string;
  labelEvery?: number;
  showValues?: boolean;
  /** Click or Enter on a bar (e.g. to filter a table below). */
  onSelect?: (d: BarDatum) => void;
  /** Highlight one bar and dim the rest. */
  selected?: string | null;
  /** Show each bar's share of this total in the tooltip. */
  total?: number;
}) {
  const tip = useTip();
  const [box, W] = useWidth();
  const padL = 30;
  const padB = 26;
  const padT = 16;
  const max = Math.max(1, ...data.map((d) => d.value));
  const nice = niceMax(max);
  const plotH = height - padB - padT;
  const step = (W - padL) / Math.max(1, data.length);
  const barW = Math.max(4, Math.min(46, step * 0.62));
  const ticks = [0, nice / 2, nice];
  const sum = total ?? data.reduce((a, d) => a + d.value, 0);
  const content = (d: BarDatum) => (
    <>
      <strong>{d.label}</strong>
      <div>{fmt(d.value)}{unit}{sum > 0 && !unit ? ` · ${Math.round((100 * d.value) / sum)}%` : ""}{d.hint ? ` · ${d.hint}` : ""}</div>
      {onSelect && <div className="faint">{selected === d.label ? "Click to clear" : "Click to filter"}</div>}
    </>
  );
  return (
    <div className="viz" ref={box}>
      <svg viewBox={`0 0 ${W} ${height}`} width="100%" role="img" aria-label="Bar chart">
        {ticks.map((t) => {
          const y = padT + plotH - (t / nice) * plotH;
          return (
            <g key={t}>
              <line x1={padL} x2={W} y1={y} y2={y} className="viz-grid" />
              <text x={padL - 6} y={y + 4} textAnchor="end" className="viz-axis">{fmt(t)}</text>
            </g>
          );
        })}
        {data.map((d, i) => {
          const h = (d.value / nice) * plotH;
          const x = padL + i * step + (step - barW) / 2;
          const y = padT + plotH - h;
          const pick = () => onSelect?.(d);
          return (
            <g key={`${i}-${d.label}`} onMouseMove={(e) => tip.show(e, content(d))} onMouseLeave={tip.hide}
              onClick={onSelect ? pick : undefined} style={onSelect ? { cursor: "pointer" } : undefined}>
              <rect x={padL + i * step} y={padT} width={step} height={plotH} fill="transparent" />
              <path d={roundedTop(x, y, barW, Math.max(h, d.value ? 2 : 0), 4)} fill={d.color ?? "var(--signal)"}
                className={`viz-bar ${onSelect ? "pick" : ""} ${selected && selected !== d.label ? "dim" : ""}`}
                style={{ animationDelay: `${Math.min(i, 12) * 30}ms` }}
                tabIndex={0} role={onSelect ? "button" : "img"} aria-label={`${d.label}: ${fmt(d.value)}${unit}`}
                aria-pressed={onSelect ? selected === d.label : undefined}
                onFocus={(e) => tip.showAt(e.currentTarget, content(d))} onBlur={tip.hide}
                onKeyDown={(e) => { if (onSelect && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); pick(); } }} />
              {showValues && d.value > 0 && (
                <text x={x + barW / 2} y={y - 5} textAnchor="middle" className="viz-value">{fmt(d.value)}</text>
              )}
              {i % labelEvery === 0 && (
                <text x={x + barW / 2} y={height - 8} textAnchor="middle" className="viz-axis">{d.label}</text>
              )}
            </g>
          );
        })}
      </svg>
      {tip.node}
    </div>
  );
}

// ---------------------------------------------------------------- line chart (one or more series)
export interface LineSeries {
  name: string;
  color?: string;
  points: { label: string; value: number }[];
}

const SERIES_COLORS = ["var(--signal)", "var(--tier-super)", "var(--tier-psu)", "var(--tier-regular)"];

export function LineChart({
  points,
  series,
  height = 180,
  unit = "",
}: {
  points?: { label: string; value: number }[];
  series?: LineSeries[];
  height?: number;
  unit?: string;
}) {
  const tip = useTip();
  const [hover, setHover] = useState<number | null>(null);
  const [off, setOff] = useState<Set<string>>(new Set());
  const all: LineSeries[] = (series ?? [{ name: "", points: points ?? [] }]).map((s, i) => ({
    ...s, color: s.color ?? SERIES_COLORS[i % SERIES_COLORS.length],
  }));
  const shown = all.filter((s) => !off.has(s.name));
  const labels = all[0]?.points.map((p) => p.label) ?? [];
  const [box, W] = useWidth();
  const padL = 30;
  const padB = 26;
  const padT = 14;
  const nice = niceMax(Math.max(1, ...shown.flatMap((s) => s.points.map((p) => p.value))));
  const plotH = height - padB - padT;
  const n = labels.length;
  const x = (i: number) => padL + (n <= 1 ? (W - padL) / 2 : (i / (n - 1)) * (W - padL - 8));
  const y = (v: number) => padT + plotH - (v / nice) * plotH;
  const every = Math.max(1, Math.ceil(n / Math.max(4, Math.floor(W / 90))));
  const multi = all.length > 1;
  const at = (i: number) => (
    <>
      <strong>{labels[i]}</strong>
      {shown.map((s) => s.points[i] && (
        <div key={s.name}>{multi && <i className="dot" style={{ background: s.color }} />}{multi ? `${s.name}: ` : ""}{fmt(s.points[i].value)}{unit}</div>
      ))}
    </>
  );
  const toggle = (name: string) => setOff((o) => {
    const nx = new Set(o);
    if (nx.has(name)) nx.delete(name);
    else if (nx.size < all.length - 1) nx.add(name); // keep at least one series visible
    return nx;
  });
  return (
    <div className="viz" ref={box}>
      {multi && <ToggleLegend items={all.map((s) => ({ label: s.name, color: s.color! }))} off={off} onToggle={toggle} />}
      <svg viewBox={`0 0 ${W} ${height}`} width="100%" role="img" aria-label="Line chart" tabIndex={0}
        onMouseLeave={() => { setHover(null); tip.hide(); }}
        onKeyDown={(e) => {
          if (!n) return;
          if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
            e.preventDefault();
            const i = hover === null ? 0 : Math.max(0, Math.min(n - 1, hover + (e.key === "ArrowRight" ? 1 : -1)));
            setHover(i);
            tip.showAt(e.currentTarget, at(i));
          }
        }}
        onBlur={() => { setHover(null); tip.hide(); }}>
        {[0, nice / 2, nice].map((t) => (
          <g key={t}>
            <line x1={padL} x2={W} y1={y(t)} y2={y(t)} className="viz-grid" />
            <text x={padL - 6} y={y(t) + 4} textAnchor="end" className="viz-axis">{fmt(t)}</text>
          </g>
        ))}
        {shown.map((s) => s.points.length > 0 && (
          <path key={s.name} d={s.points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.value)}`).join(" ")} fill="none"
            stroke={s.color} strokeWidth={2} strokeLinejoin="round" className="viz-line" />
        ))}
        {labels.map((label, i) => (
          <g key={`${label}-${i}`}>
            {i % every === 0 && <text x={x(i)} y={height - 8} textAnchor="middle" className="viz-axis">{label}</text>}
            <rect x={x(i) - (W / Math.max(n, 1)) / 2} y={padT} width={W / Math.max(n, 1)} height={plotH}
              fill="transparent" onMouseMove={(e) => { setHover(i); tip.show(e, at(i)); }} />
          </g>
        ))}
        {hover !== null && hover < n && (
          <g pointerEvents="none">
            <line x1={x(hover)} x2={x(hover)} y1={padT} y2={padT + plotH} className="viz-cross" />
            {shown.map((s) => s.points[hover] && (
              <circle key={s.name} cx={x(hover)} cy={y(s.points[hover].value)} r={5} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
            ))}
          </g>
        )}
        {!multi && shown[0] && shown[0].points.length > 0 && (
          <text x={x(n - 1)} y={y(shown[0].points[n - 1].value) - 8} textAnchor="end" className="viz-value">
            {fmt(shown[0].points[n - 1].value)}{unit}
          </text>
        )}
      </svg>
      {tip.node}
    </div>
  );
}

// ---------------------------------------------------------------- cutoff strip ("where you stand")
export function CutoffStrip({
  rows,
  mine,
  min = 5,
  max = 10,
}: {
  rows: { label: string; value: number | null; color: string; ok: boolean | null; sub?: string; href?: string }[];
  mine: number;
  min?: number;
  max?: number;
}) {
  const pos = (v: number) => `${((Math.min(max, Math.max(min, v)) - min) / (max - min)) * 100}%`;
  return (
    <div className="strip">
      <div className="strip-axis">
        <span />
        <div className="strip-scale">
          {[5, 6, 7, 8, 9, 10].map((t) => <span key={t} style={{ left: pos(t) }}>{t}</span>)}
          <span className="strip-me-label" style={{ left: pos(mine) }}>You {mine.toFixed(2)}</span>
        </div>
      </div>
      {rows.map((r) => (
        <div className="strip-row" key={r.label} title={r.value ? `${r.label}: cutoff ${r.value}` : `${r.label}: no CGPA cutoff`}>
          <span className="strip-name">
            {r.href ? <Link to={r.href}>{r.label}</Link> : r.label}
            {r.sub && <em>{r.sub}</em>}
          </span>
          <div className="strip-track">
            <div className="strip-me" style={{ left: pos(mine) }} aria-hidden />
            {r.value !== null ? (
              <>
                <div className="strip-range" style={{ left: pos(r.value), right: 0, background: r.color }} />
                <div className="strip-dot" style={{ left: pos(r.value), borderColor: r.color }} />
              </>
            ) : <span className="strip-none">no cutoff</span>}
          </div>
          <span className={`strip-flag ${r.ok === false ? "no" : r.ok ? "ok" : ""}`}>
            {r.ok === null ? "—" : r.ok ? "Eligible" : "Not eligible"}
          </span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- timeline (Gantt)
export interface TimelineItem {
  id: number;
  lane: string;
  start: string;
  end: string;
  label: string;
  status: string;
  late?: number;
}

export function Timeline({ items, nowIso, range, legend = true }: {
  items: TimelineItem[];
  nowIso?: string;
  /** Fixed [from, to] in epoch ms, so several day strips share one scale. */
  range?: [number, number];
  legend?: boolean;
}) {
  const tip = useTip();
  const lanes = useMemo(() => [...new Set(items.map((i) => i.lane))], [items]);
  if (!items.length) return <p className="muted">No interview slots yet.</p>;
  const t0 = Math.min(range?.[0] ?? Infinity, ...items.map((i) => Date.parse(i.start)));
  const t1 = Math.max(range?.[1] ?? -Infinity, ...items.map((i) => Date.parse(i.end)));
  const from = Math.floor(t0 / 1800000) * 1800000;
  const to = Math.ceil(t1 / 1800000) * 1800000;
  const span = Math.max(1, to - from);
  const pct = (t: number) => `${((t - from) / span) * 100}%`;
  const ticks: number[] = [];
  for (let t = from; t <= to; t += 1800000) ticks.push(t);
  const now = nowIso ? Date.parse(nowIso) : Date.now();
  return (
    <div className="viz gantt">
      <div className="gantt-head">
        <span />
        <div className="gantt-scale">
          {ticks.map((t, i) => (i % 2 === 0 && t < to ? (
            <span key={t} style={{ left: pct(t) }}>{new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
          ) : null))}
        </div>
      </div>
      {lanes.map((lane) => (
        <div className="gantt-row" key={lane}>
          <span className="gantt-lane">{lane}</span>
          <div className="gantt-track">
            {ticks.map((t) => <i key={t} className="gantt-tick" style={{ left: pct(t) }} />)}
            {now >= from && now <= to && <i className="gantt-now" style={{ left: pct(now) }} title="Now" />}
            {items.filter((i) => i.lane === lane).map((i) => (
              <div key={i.id} className={`gantt-bar s-${i.status}`}
                style={{ left: pct(Date.parse(i.start)), width: `calc(${((Date.parse(i.end) - Date.parse(i.start)) / span) * 100}% - 2px)` }}
                onMouseMove={(e) => tip.show(e, <><strong>{i.label}</strong><div>{new Date(i.start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}–{new Date(i.end).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · {i.status.replace("_", " ")}{i.late ? ` · +${i.late} min` : ""}</div></>)}
                onMouseLeave={tip.hide}>
                <span>{i.label}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
      {legend && <Legend items={[
        { label: "Done", color: "var(--gantt-done)" },
        { label: "In the room", color: "var(--gantt-live)" },
        { label: "Waiting", color: "var(--gantt-wait)" },
        { label: "Absent", color: "var(--gantt-absent)" },
      ]} />}
      {tip.node}
    </div>
  );
}

// ---------------------------------------------------------------- month calendar
export interface CalEvent {
  kind: "interview" | "deadline" | "drive" | "offer";
  start: string | null;
  end: string | null;
  title: string;
  detail: string;
  status: string;
  link: string;
}

const KIND_LABEL: Record<CalEvent["kind"], string> = {
  interview: "Interview",
  deadline: "Deadline",
  drive: "Drive day",
  offer: "Offer deadline",
};

function dayKey(d: Date): string {
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

export function MonthCalendar({ events }: { events: CalEvent[] }) {
  const today = new Date();
  const [cursor, setCursor] = useState(() => new Date(today.getFullYear(), today.getMonth(), 1));
  const [selected, setSelected] = useState<string>(dayKey(today));
  const byDay = useMemo(() => {
    const m = new Map<string, CalEvent[]>();
    for (const e of events) {
      if (!e.start) continue;
      const k = dayKey(new Date(e.start));
      m.set(k, [...(m.get(k) ?? []), e]);
    }
    return m;
  }, [events]);
  const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
  const offset = (first.getDay() + 6) % 7; // weeks start on Monday
  const days = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 0).getDate();
  const cells: (Date | null)[] = [...Array(offset).fill(null),
    ...Array.from({ length: days }, (_, i) => new Date(cursor.getFullYear(), cursor.getMonth(), i + 1))];
  while (cells.length % 7) cells.push(null);
  const agenda = byDay.get(selected) ?? [];
  const selDate = (() => {
    const [y, m, d] = selected.split("-").map(Number);
    return new Date(y, m, d);
  })();

  return (
    <div className="cal">
      <div className="cal-grid-wrap">
        <div className="cal-head">
          <button className="btn ghost small" aria-label="Previous month" onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))}><ChevronLeft size={16} /></button>
          <strong>{cursor.toLocaleDateString([], { month: "long", year: "numeric" })}</strong>
          <button className="btn ghost small" aria-label="Next month" onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))}><ChevronRight size={16} /></button>
          <span className="spacer" />
          <button className="btn small" onClick={() => { setCursor(new Date(today.getFullYear(), today.getMonth(), 1)); setSelected(dayKey(today)); }}>Today</button>
        </div>
        <div className="cal-grid" role="grid">
          {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => <div key={d} className="cal-dow">{d}</div>)}
          {cells.map((c, i) => {
            if (!c) return <div key={`x${i}`} className="cal-cell empty" />;
            const k = dayKey(c);
            const evs = byDay.get(k) ?? [];
            return (
              <button key={k} role="gridcell" className={`cal-cell ${k === dayKey(today) ? "today" : ""} ${k === selected ? "sel" : ""}`}
                onClick={() => setSelected(k)} aria-label={`${c.toDateString()}, ${evs.length} events`}>
                <span className="cal-num">{c.getDate()}</span>
                <span className="cal-dots">
                  {evs.slice(0, 3).map((e, j) => <span key={j} className={`cal-chip k-${e.kind}`}>{e.title.split(":")[0].split(" applications")[0]}</span>)}
                  {evs.length > 3 && <span className="cal-more">+{evs.length - 3}</span>}
                </span>
              </button>
            );
          })}
        </div>
        <Legend items={[
          { label: "Interview", color: "var(--cal-interview)" },
          { label: "Application deadline", color: "var(--cal-deadline)" },
          { label: "Drive day", color: "var(--cal-drive)" },
          { label: "Offer deadline", color: "var(--cal-offer)" },
        ]} />
      </div>
      <div className="cal-agenda">
        <h3>{selDate.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" })}</h3>
        {agenda.length === 0 ? <p className="muted small" style={{ marginTop: 8 }}>Nothing on this day.</p> : agenda.map((e, i) => (
          <Link key={i} to={e.link} className={`cal-item k-${e.kind}`}>
            <span className="cal-time">{e.start ? new Date(e.start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}</span>
            <span>
              <strong>{e.title}</strong>
              <span className="small muted">{KIND_LABEL[e.kind]} · {e.detail}</span>
            </span>
          </Link>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- helpers
function niceMax(v: number): number {
  if (v <= 5) return Math.max(1, Math.ceil(v));
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

function fmt(v: number): string {
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}

function roundedTop(x: number, y: number, w: number, h: number, r: number): string {
  if (h <= 0) return "";
  const rr = Math.min(r, w / 2, h);
  return `M${x},${y + h} V${y + rr} Q${x},${y} ${x + rr},${y} H${x + w - rr} Q${x + w},${y} ${x + w},${y + rr} V${y + h} Z`;
}
