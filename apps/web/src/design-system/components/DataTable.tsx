import clsx from "clsx";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Button } from "./Button";
import { SearchInput } from "./Form";

export interface Column<T> {
  key: string;
  header: ReactNode;
  value: (row: T) => unknown; // used for sorting and search
  render?: (row: T) => ReactNode;
  numeric?: boolean;
  width?: number | string;
  sortable?: boolean;
}

function compare(a: unknown, b: unknown): number {
  if (a === b) return 0;
  if (a === null || a === undefined) return 1; // nulls last
  if (b === null || b === undefined) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
}

/**
 * Sortable, searchable, paginated table. Used both for app lists and as the
 * accessible "table view" twin of every chart.
 */
export function DataTable<T>({
  rows,
  columns,
  rowKey,
  pageSize = 25,
  searchable = false,
  dense = false,
  onRowClick,
  isSelected,
  empty,
  initialSort,
  caption,
}: {
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T, index: number) => string;
  pageSize?: number;
  searchable?: boolean;
  dense?: boolean;
  onRowClick?: (row: T) => void;
  isSelected?: (row: T) => boolean;
  empty?: ReactNode;
  initialSort?: { key: string; dir: "asc" | "desc" };
  caption?: string;
}) {
  const [sort, setSort] = useState(initialSort ?? null);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);

  const filtered = useMemo(() => {
    if (!query.trim()) return rows;
    const q = query.toLowerCase();
    return rows.filter((r) => columns.some((c) => String(c.value(r) ?? "").toLowerCase().includes(q)));
  }, [rows, columns, query]);

  const sorted = useMemo(() => {
    if (!sort) return filtered;
    const col = columns.find((c) => c.key === sort.key);
    if (!col) return filtered;
    const out = [...filtered].sort((a, b) => compare(col.value(a), col.value(b)));
    return sort.dir === "desc" ? out.reverse() : out;
  }, [filtered, sort, columns]);

  const pages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const current = Math.min(page, pages - 1);
  const visible = sorted.slice(current * pageSize, current * pageSize + pageSize);

  const toggle = (key: string) =>
    setSort((s) => (s?.key === key ? (s.dir === "asc" ? { key, dir: "desc" } : null) : { key, dir: "asc" }));

  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: 0, height: "100%" }}>
      {searchable && (
        <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--divider)" }}>
          <SearchInput
            size_="sm"
            placeholder="Search rows"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(0);
            }}
            aria-label="Search rows"
          />
        </div>
      )}
      <div className="iq-table-wrap" style={{ flex: 1 }}>
        <table className={clsx("iq-table", dense && "iq-table--dense")}>
          {caption && <caption className="sr-only">{caption}</caption>}
          <thead>
            <tr>
              {columns.map((c) => (
                <th
                  key={c.key}
                  className={c.numeric ? "is-num" : undefined}
                  style={{ width: c.width }}
                  aria-sort={sort?.key === c.key ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}
                >
                  {c.sortable === false ? (
                    c.header
                  ) : (
                    <button onClick={() => toggle(c.key)}>
                      {c.header}
                      {sort?.key === c.key && (sort.dir === "asc" ? <ArrowUp aria-hidden /> : <ArrowDown aria-hidden />)}
                    </button>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((row, i) => (
              <tr
                key={rowKey(row, current * pageSize + i)}
                data-clickable={onRowClick ? "true" : undefined}
                data-selected={isSelected?.(row) ? "true" : undefined}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                tabIndex={onRowClick ? 0 : undefined}
                onKeyDown={onRowClick ? (e) => e.key === "Enter" && onRowClick(row) : undefined}
              >
                {columns.map((c) => (
                  <td key={c.key} className={c.numeric ? "is-num" : undefined}>
                    {c.render ? c.render(row) : String(c.value(row) ?? "")}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {visible.length === 0 && (empty ?? <div className="iq-state iq-state--compact">No rows</div>)}
      </div>
      {pages > 1 && (
        <div className="iq-pager">
          <span>
            {current * pageSize + 1}–{Math.min(sorted.length, (current + 1) * pageSize)} of {sorted.length.toLocaleString()}
          </span>
          <span style={{ display: "flex", gap: 4 }}>
            <Button size="sm" variant="ghost" icon={<ChevronLeft />} disabled={current === 0} onClick={() => setPage(current - 1)} aria-label="Previous page" />
            <Button size="sm" variant="ghost" icon={<ChevronRight />} disabled={current >= pages - 1} onClick={() => setPage(current + 1)} aria-label="Next page" />
          </span>
        </div>
      )}
    </div>
  );
}
