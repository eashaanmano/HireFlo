// Interactive table: search, column filters, sorting, pagination, column visibility, row selection and CSV export.
// Works on the rows it is given (client side), so pages pass everything they fetched.
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight, Download, SlidersHorizontal, Search, X } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

export interface Column<T> {
  key: string;
  header: ReactNode;
  /** Plain value for sorting, searching, filtering and CSV. */
  value?: (row: T) => string | number | boolean | null | undefined;
  render?: (row: T) => ReactNode;
  align?: "left" | "right" | "center";
  sortable?: boolean;
  /** Show a dropdown of this column's distinct values. */
  filter?: boolean;
  /** Start hidden (the user can show it from Columns). */
  hidden?: boolean;
  /** Can't be hidden. */
  pinned?: boolean;
  csv?: boolean;
  width?: number | string;
  csvHeader?: string;
}

type Dir = "asc" | "desc";

export function cellText(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  return String(v);
}

/** CSV for the given rows and columns (RFC 4180 quoting; formula-looking cells are prefixed to stay inert). */
export function toCsv<T>(rows: T[], columns: Column<T>[]): string {
  const cols = columns.filter((c) => c.value && c.csv !== false);
  const esc = (s: string) => {
    const safe = /^[=+\-@\t\r]/.test(s) ? `'${s}` : s;
    return /[",\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
  };
  const head = cols.map((c) => esc(c.csvHeader ?? (typeof c.header === "string" ? c.header : c.key)));
  const body = rows.map((r) => cols.map((c) => esc(cellText(c.value!(r)))).join(","));
  return [head.join(","), ...body].join("\r\n");
}

export function saveCsv(filename: string, csv: string) {
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename.endsWith(".csv") ? filename : `${filename}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function compare(a: unknown, b: unknown): number {
  if (a === b) return 0;
  if (a === null || a === undefined || a === "") return 1; // blanks last
  if (b === null || b === undefined || b === "") return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
}

/** Filter, search and sort rows (exported for tests). */
export function applyView<T>(rows: T[], columns: Column<T>[], search: string, filters: Record<string, string>,
  sort: { key: string; dir: Dir } | null): T[] {
  const q = search.trim().toLowerCase();
  let out = rows.filter((r) =>
    Object.entries(filters).every(([k, v]) => {
      if (!v) return true;
      const col = columns.find((c) => c.key === k);
      return col?.value ? cellText(col.value(r)) === v : true;
    }),
  );
  if (q) {
    const searchable = columns.filter((c) => c.value);
    out = out.filter((r) => searchable.some((c) => cellText(c.value!(r)).toLowerCase().includes(q)));
  }
  if (sort) {
    const col = columns.find((c) => c.key === sort.key);
    if (col?.value) {
      const sign = sort.dir === "asc" ? 1 : -1;
      out = [...out].sort((a, b) => {
        const va = col.value!(a);
        const vb = col.value!(b);
        const blankA = va === null || va === undefined || va === "";
        const blankB = vb === null || vb === undefined || vb === "";
        if (blankA || blankB) return blankA === blankB ? 0 : blankA ? 1 : -1;
        return sign * compare(va, vb);
      });
    }
  }
  return out;
}

export function DataTable<T>({
  rows,
  columns,
  rowKey,
  search = true,
  searchPlaceholder = "Search…",
  pageSize = 25,
  initialSort,
  exportName,
  selection,
  onRowClick,
  rowClassName,
  empty = "Nothing to show.",
  toolbar,
  maxHeight,
  externalFilter,
  initialSearch = "",
}: {
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T) => string | number;
  search?: boolean;
  searchPlaceholder?: string;
  pageSize?: number;
  initialSort?: { key: string; dir: Dir };
  exportName?: string;
  selection?: { selected: Set<number>; onChange: (next: Set<number>) => void; id: (row: T) => number };
  onRowClick?: (row: T) => void;
  rowClassName?: (row: T) => string | undefined;
  empty?: ReactNode;
  toolbar?: ReactNode;
  maxHeight?: number;
  /** Filters set from outside, e.g. by clicking a chart bar: { columnKey: value }. */
  externalFilter?: Record<string, string>;
  initialSearch?: string;
}) {
  const [q, setQ] = useState(initialSearch);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [sort, setSort] = useState<{ key: string; dir: Dir } | null>(initialSort ?? null);
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(pageSize);
  const [hidden, setHidden] = useState<Set<string>>(() => new Set(columns.filter((c) => c.hidden).map((c) => c.key)));
  const [showCols, setShowCols] = useState(false);

  const ext = externalFilter ?? {};
  const extSig = JSON.stringify(ext);
  useEffect(() => {
    setFilters((f) => ({ ...f, ...ext }));
  }, [extSig]);

  const visible = columns.filter((c) => !hidden.has(c.key));
  const view = useMemo(() => applyView(rows, columns, q, filters, sort), [rows, columns, q, filters, sort]);
  const pages = size > 0 ? Math.max(1, Math.ceil(view.length / size)) : 1;
  const current = Math.min(page, pages - 1);
  const shown = size > 0 ? view.slice(current * size, current * size + size) : view;
  useEffect(() => {
    setPage(0);
  }, [q, filters, size]);

  const options = useMemo(() => {
    const out: Record<string, string[]> = {};
    for (const c of columns.filter((c) => c.filter && c.value)) {
      out[c.key] = [...new Set(rows.map((r) => cellText(c.value!(r))).filter(Boolean))].sort((a, b) =>
        a.localeCompare(b, undefined, { numeric: true }));
    }
    return out;
  }, [rows, columns]);

  const toggleSort = (key: string) =>
    setSort((s) => (s?.key !== key ? { key, dir: "asc" } : s.dir === "asc" ? { key, dir: "desc" } : null));
  const active = Object.values(filters).filter(Boolean).length + (q ? 1 : 0);

  const sel = selection;
  const viewIds = sel ? view.map(sel.id) : [];
  const allSelected = sel ? viewIds.length > 0 && viewIds.every((id) => sel.selected.has(id)) : false;
  const toggleAll = () => {
    if (!sel) return;
    const next = new Set(sel.selected);
    if (allSelected) viewIds.forEach((id) => next.delete(id));
    else viewIds.forEach((id) => next.add(id));
    sel.onChange(next);
  };
  const toggleOne = (id: number) => {
    if (!sel) return;
    const next = new Set(sel.selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    sel.onChange(next);
  };

  return (
    <div className="dt">
      <div className="dt-bar">
        {search && (
          <label className="dt-search">
            <Search size={15} aria-hidden />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={searchPlaceholder} aria-label="Search table" />
            {q && <button className="icon-btn" onClick={() => setQ("")} aria-label="Clear search"><X size={13} /></button>}
          </label>
        )}
        {columns.filter((c) => c.filter && c.value && (!hidden.has(c.key) || filters[c.key])).map((c) => (
          <select key={c.key} value={filters[c.key] ?? ""} aria-label={`Filter by ${typeof c.header === "string" ? c.header : c.key}`}
            onChange={(e) => setFilters((f) => ({ ...f, [c.key]: e.target.value }))} className={filters[c.key] ? "on" : ""}>
            <option value="">{typeof c.header === "string" ? `${c.header}: all` : "All"}</option>
            {options[c.key]?.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        ))}
        {active > 0 && (
          <button className="icon-btn" onClick={() => { setQ(""); setFilters({}); }}><X size={13} /> Clear filters</button>
        )}
        <span className="dt-spacer" />
        {toolbar}
        <div className="dt-cols">
          <button className="btn small ghost" onClick={() => setShowCols((s) => !s)} aria-expanded={showCols} title="Show or hide columns">
            <SlidersHorizontal size={14} /> Columns
          </button>
          {showCols && (
            <div className="dt-menu" role="menu">
              {columns.filter((c) => !c.pinned).map((c) => (
                <label key={c.key} className="row small">
                  <input type="checkbox" checked={!hidden.has(c.key)} style={{ width: "auto" }}
                    onChange={() => setHidden((h) => { const n = new Set(h); if (n.has(c.key)) n.delete(c.key); else n.add(c.key); return n; })} />
                  {typeof c.header === "string" ? c.header : c.key}
                </label>
              ))}
            </div>
          )}
        </div>
        {exportName && (
          <button className="btn small ghost" onClick={() => saveCsv(exportName, toCsv(view, columns))} title="Download what is shown, all pages, as CSV">
            <Download size={14} /> CSV
          </button>
        )}
      </div>
      <div className="table-wrap dt-wrap" style={maxHeight ? { maxHeight, overflowY: "auto" } : undefined}>
        <table>
          <thead>
            <tr>
              {sel && (
                <th style={{ width: 32 }}>
                  <input type="checkbox" checked={allSelected} onChange={toggleAll} aria-label="Select all shown" style={{ width: "auto" }} />
                </th>
              )}
              {visible.map((c) => {
                const sortable = c.sortable ?? Boolean(c.value);
                const dir = sort?.key === c.key ? sort.dir : null;
                return (
                  <th key={c.key} style={{ textAlign: c.align ?? "left", width: c.width }}
                    aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : undefined}>
                    {sortable ? (
                      <button className={`dt-sort ${dir ? "on" : ""}`} onClick={() => toggleSort(c.key)}>
                        {c.header}
                        {dir === "asc" ? <ArrowUp size={12} /> : dir === "desc" ? <ArrowDown size={12} /> : <ArrowUpDown size={12} className="dt-idle" />}
                      </button>
                    ) : c.header}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {shown.length === 0 ? (
              <tr><td colSpan={visible.length + (sel ? 1 : 0)} className="muted" style={{ textAlign: "center", padding: 24 }}>
                {rows.length && active ? "No rows match these filters." : empty}
              </td></tr>
            ) : shown.map((r) => {
              const id = sel?.id(r);
              return (
                <tr key={rowKey(r)} className={[onRowClick ? "click" : "", id !== undefined && sel?.selected.has(id) ? "selected" : "", rowClassName?.(r) ?? ""].join(" ")}
                  onClick={onRowClick ? () => onRowClick(r) : undefined}>
                  {sel && id !== undefined && (
                    <td onClick={(e) => e.stopPropagation()}>
                      <input type="checkbox" checked={sel.selected.has(id)} onChange={() => toggleOne(id)} aria-label="Select row" style={{ width: "auto" }} />
                    </td>
                  )}
                  {visible.map((c) => (
                    <td key={c.key} style={{ textAlign: c.align ?? "left" }} className={c.align === "right" ? "num" : undefined}>
                      {c.render ? c.render(r) : cellText(c.value?.(r)) || "—"}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="dt-foot">
        <span className="small muted">
          {view.length === rows.length ? `${rows.length} rows` : `${view.length} of ${rows.length} rows`}
          {sel && sel.selected.size > 0 ? ` · ${sel.selected.size} selected` : ""}
        </span>
        {size > 0 && view.length > Math.min(...[10, size]) && (
          <span className="row" style={{ gap: 6 }}>
            <select value={size} onChange={(e) => setSize(Number(e.target.value))} aria-label="Rows per page" style={{ width: "auto" }}>
              {[10, 25, 50, 100].map((n) => <option key={n} value={n}>{n} / page</option>)}
            </select>
            <button className="btn small ghost" disabled={current === 0} onClick={() => setPage(current - 1)} aria-label="Previous page"><ChevronLeft size={14} /></button>
            <span className="small">{current + 1} / {pages}</span>
            <button className="btn small ghost" disabled={current >= pages - 1} onClick={() => setPage(current + 1)} aria-label="Next page"><ChevronRight size={14} /></button>
          </span>
        )}
      </div>
    </div>
  );
}
