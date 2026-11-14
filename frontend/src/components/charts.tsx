import { useState, type ReactNode } from "react";
import type { FunnelStage } from "../api/types";
import { DataTable, saveCsv, toCsv, type Column } from "./DataTable";
import { Card } from "./ui";

/** Horizontal funnel; each bar is proportional to the first stage so drop-offs are visible at a glance. */
export function Funnel({ stages, target }: { stages: FunnelStage[]; target?: number }) {
  const top = Math.max(1, stages[0]?.count ?? 0, target ?? 0);
  return (
    <div className="funnel" role="list" aria-label="Funnel">
      {stages.map((s, i) => {
        const prev = i > 0 ? stages[i - 1].count : null;
        const conv = prev ? Math.round((100 * s.count) / prev) : null;
        const ofTop = stages[0]?.count ? Math.round((100 * s.count) / stages[0].count) : null;
        return (
        <div className="funnel-row" role="listitem" key={s.stage}
          title={i === 0 ? `${s.stage}: ${s.count}` : `${s.stage}: ${s.count} (${conv ?? 0}% of the previous stage, ${ofTop ?? 0}% of ${stages[0].stage.toLowerCase()})`}>
          <span>{s.stage}{conv !== null && <span className="conv">{conv}%</span>}</span>
          <div className="funnel-track">
            <div
              className="funnel-bar"
              style={{ width: `${(100 * s.count) / top}%`, opacity: s.count ? 1 : 0.25 }}
              data-testid={`bar-${s.stage}`}
            />
          </div>
          <span className="n">{s.count}</span>
        </div>
        );
      })}
      {target !== undefined && (
        <p className="small muted">
          Target: {target} hire{target === 1 ? "" : "s"}
        </p>
      )}
    </div>
  );
}

export function HBars({
  rows,
  max,
  alt,
  onSelect,
  selected,
}: {
  rows: { label: string; value: number; note?: string }[];
  max?: number;
  alt?: boolean;
  /** Click a row (e.g. to filter a table); click again to clear. */
  onSelect?: (label: string | null) => void;
  selected?: string | null;
}) {
  const top = Math.max(1, max ?? Math.max(0, ...rows.map((r) => r.value)));
  return (
    <div className="stack" style={{ gap: 8 }}>
      {rows.map((r) => {
        const pick = () => onSelect?.(selected === r.label ? null : r.label);
        return (
          <div className={`hbar ${onSelect ? "pick" : ""} ${selected === r.label ? "on" : ""}`} key={r.label}
            title={`${r.label}: ${r.note ?? r.value}${onSelect ? (selected === r.label ? " (click to clear)" : " (click to filter)") : ""}`}
            onClick={onSelect ? pick : undefined} role={onSelect ? "button" : undefined} tabIndex={onSelect ? 0 : undefined}
            aria-pressed={onSelect ? selected === r.label : undefined}
            onKeyDown={onSelect ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } } : undefined}>
            <span>{r.label}</span>
            <div className="track">
              <div className={`fill ${alt ? "alt" : ""}`} style={{ width: `${(100 * r.value) / top}%`, opacity: selected && selected !== r.label ? 0.4 : 1 }} />
            </div>
            <span className="num small" style={{ whiteSpace: "nowrap" }}>{r.note ?? r.value}</span>
          </div>
        );
      })}
    </div>
  );
}

/**
 * A card that shows a chart with a Chart / Table switch and a CSV download of the same numbers, so every chart
 * can also be read exactly and reused in reports.
 */
export function VizCard<R>({
  title,
  subtitle,
  className,
  rows,
  columns,
  filename,
  extra,
  children,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  className?: string;
  rows: R[];
  columns: Column<R>[];
  filename: string;
  extra?: ReactNode;
  children: ReactNode;
}) {
  const [mode, setMode] = useState<"chart" | "table">("chart");
  return (
    <Card title={title} subtitle={subtitle} className={className}
      action={
        <div className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
          {extra}
          <div className="seg" role="group" aria-label="View">
            <button className={mode === "chart" ? "on" : ""} onClick={() => setMode("chart")}>Chart</button>
            <button className={mode === "table" ? "on" : ""} onClick={() => setMode("table")}>Table</button>
          </div>
          <button className="btn small ghost" onClick={() => saveCsv(filename, toCsv(rows, columns))} title="Download as CSV">CSV</button>
        </div>
      }>
      {mode === "chart" ? children : (
        <DataTable rows={rows} columns={columns} rowKey={(r) => String(columns[0]?.value?.(r) ?? JSON.stringify(r))}
          search={false} pageSize={0} />
      )}
    </Card>
  );
}

/** Side-by-side comparison of two values (used by the simulator report). */
export function Compare({
  label,
  before,
  after,
  unit,
}: {
  label: string;
  before: number;
  after: number;
  unit: string;
}) {
  const top = Math.max(before, after, 1);
  const better = after < before;
  const pct = before ? Math.round((100 * Math.abs(before - after)) / before) : null;
  const verdict = pct === null ? "—" : pct === 0 ? "about the same" : `${pct}% ${better ? "lower" : "higher"} with re-planning`;
  return (
    <div className="stack" style={{ gap: 4 }}>
      <div className="row between small">
        <strong>{label}</strong>
        <span style={{ color: pct ? (better ? "var(--green)" : "var(--amber)") : undefined }}>{verdict}</span>
      </div>
      <div className="hbar">
        <span className="small muted">Fixed plan</span>
        <div className="track">
          <div className="fill alt" style={{ width: `${(100 * before) / top}%` }} />
        </div>
        <span className="num small" style={{ whiteSpace: "nowrap" }}>
          {before} {unit}
        </span>
      </div>
      <div className="hbar">
        <span className="small muted">With re-planning</span>
        <div className="track">
          <div className="fill" style={{ width: `${(100 * after) / top}%` }} />
        </div>
        <span className="num small" style={{ whiteSpace: "nowrap" }}>
          {after} {unit}
        </span>
      </div>
    </div>
  );
}
