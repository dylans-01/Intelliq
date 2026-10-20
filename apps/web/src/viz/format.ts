import type { ColumnMeta, Format, Scalar } from "@/api/types";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** Parse an ISO date/datetime from the API without timezone drift. */
export function parseDate(v: string): { y: number; m: number; d: number; time?: string } | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}:\d{2}(?::\d{2})?))?/.exec(v);
  if (!match) return null;
  return { y: +match[1], m: +match[2], d: +match[3], time: match[4] };
}

export function formatDate(v: string, unit?: string | null): string {
  const p = parseDate(v);
  if (!p) return v;
  switch (unit) {
    case "year":
      return String(p.y);
    case "quarter":
      return `Q${Math.floor((p.m - 1) / 3) + 1} ${p.y}`;
    case "month":
      return `${MONTHS[p.m - 1]} ${p.y}`;
    case "week":
      return `Week of ${MONTHS[p.m - 1]} ${p.d}, ${p.y}`;
    case "hour":
    case "minute":
      return `${MONTHS[p.m - 1]} ${p.d}, ${p.y} ${p.time?.slice(0, 5) ?? ""}`.trim();
    default:
      return p.time && p.time !== "00:00:00" && p.time !== "00:00"
        ? `${MONTHS[p.m - 1]} ${p.d}, ${p.y} ${p.time.slice(0, 5)}`
        : `${MONTHS[p.m - 1]} ${p.d}, ${p.y}`;
  }
}

export function formatDatePart(v: Scalar, part?: string | null): string {
  if (v === null || v === undefined) return "Null";
  const n = Number(v);
  if (part === "month" && n >= 1 && n <= 12) return MONTHS[n - 1];
  if (part === "weekday" && n >= 1 && n <= 7) return WEEKDAYS[n - 1];
  if (part === "quarter") return `Q${n}`;
  if (part === "week") return `Week ${n}`;
  return String(v);
}

function compactNumber(n: number, decimals: number): string {
  const abs = Math.abs(n);
  const units: [number, string][] = [
    [1e12, "T"],
    [1e9, "B"],
    [1e6, "M"],
    [1e3, "K"],
  ];
  for (const [size, suffix] of units) {
    if (abs >= size) {
      const scaled = n / size;
      const d = Math.abs(scaled) >= 100 ? 0 : 1; // 1.2M, 12.5K, 250K
      return `${trimZeros(scaled.toFixed(d))}${suffix}`;
    }
  }
  return trimZeros(n.toFixed(abs >= 100 ? 0 : decimals));
}

function trimZeros(s: string): string {
  return s.includes(".") ? s.replace(/\.?0+$/, "") : s;
}

export interface FormatOptions {
  compact?: boolean;
}

/** Format a single value for display according to the column's semantic format. */
export function formatValue(value: Scalar | undefined, column: Pick<ColumnMeta, "data_type" | "format" | "time_unit" | "date_part" | "role">, opts: FormatOptions = {}): string {
  if (value === null || value === undefined) return column.role === "measure" ? "–" : "Null";
  if (column.date_part) return formatDatePart(value, column.date_part);
  if (column.data_type === "date" || column.data_type === "datetime") return formatDate(String(value), column.time_unit);
  if (typeof value === "boolean") return value ? "True" : "False";
  if (typeof value !== "number") return String(value);
  return formatNumber(value, column.format ?? null, column.data_type, opts);
}

export function formatNumber(value: number, format: Format | null, dataType = "real", opts: FormatOptions = {}): string {
  const type = format?.type ?? "number";
  const compact = opts.compact || format?.compact;
  if (type === "percent") {
    const decimals = format?.decimals ?? 1;
    return `${(value * 100).toLocaleString("en-US", { minimumFractionDigits: compact ? 0 : decimals, maximumFractionDigits: decimals })}%`;
  }
  const defaultDecimals = dataType === "integer" ? 0 : Math.abs(value) >= 1000 ? 0 : 2;
  const decimals = format?.decimals ?? defaultDecimals;
  const sign = value < 0 ? "-" : "";
  const body = compact
    ? compactNumber(Math.abs(value), decimals)
    : Math.abs(value).toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  if (type === "currency") {
    const symbol = currencySymbol(format?.currency ?? "USD");
    return `${sign}${symbol}${body}`;
  }
  return `${sign}${body}`;
}

function currencySymbol(code: string): string {
  return ({ USD: "$", EUR: "€", GBP: "£", JPY: "¥", INR: "₹", CAD: "CA$", AUD: "A$" } as Record<string, string>)[code] ?? `${code} `;
}

/** Escape text for insertion into HTML (ECharts tooltips render HTML strings). */
export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}
