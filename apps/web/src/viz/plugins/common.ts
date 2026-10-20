import type { ColumnMeta, Scalar } from "@/api/types";
import { colorFor } from "../colors";
import { escapeHtml, formatValue } from "../format";
import type { BoundField, RenderContext } from "../model";

const font = () =>
  (typeof document !== "undefined" && getComputedStyle(document.documentElement).getPropertyValue("--font-sans").trim()) ||
  "system-ui, sans-serif";

export interface TooltipRow {
  color?: string;
  shape?: "line" | "rect" | "dot";
  label: string;
  value: string;
}

/**
 * Tooltip markup. Values lead (strong), series names follow. All text is
 * escaped: labels come from data and may contain markup.
 */
export function tooltipHtml(title: string, rows: TooltipRow[]): string {
  const key = (r: TooltipRow) => {
    if (!r.color) return `<span style="display:inline-block;width:10px"></span>`;
    if (r.shape === "line") return `<span style="display:inline-block;width:10px;height:2px;border-radius:1px;background:${r.color};vertical-align:middle"></span>`;
    if (r.shape === "dot") return `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${r.color}"></span>`;
    return `<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${r.color}"></span>`;
  };
  const body = rows
    .map(
      (r) =>
        `<div style="display:flex;align-items:center;gap:8px;margin-top:4px">${key(r)}` +
        `<span style="font-weight:600;font-variant-numeric:tabular-nums">${escapeHtml(r.value)}</span>` +
        `<span style="opacity:.72">${escapeHtml(r.label)}</span></div>`,
    )
    .join("");
  return `<div style="font-size:12px;line-height:1.35;min-width:120px"><div style="font-weight:500;opacity:.8">${escapeHtml(title)}</div>${body}</div>`;
}

export function baseOption(ctx: RenderContext) {
  const p = ctx.palette;
  return {
    animationDuration: 250,
    animationDurationUpdate: 200,
    textStyle: { fontFamily: font(), color: p.ink2, fontSize: 11 },
    aria: { enabled: true },
    tooltip: {
      confine: true,
      backgroundColor: p.overlay,
      borderColor: p.border,
      borderWidth: 1,
      padding: [8, 10],
      textStyle: { color: p.ink1, fontFamily: font() },
      extraCssText: "border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.12);",
    },
  };
}

export function valueAxis(ctx: RenderContext, col: ColumnMeta | undefined, extra: Record<string, unknown> = {}) {
  const p = ctx.palette;
  return {
    type: "value",
    axisLine: { show: false },
    axisTick: { show: false },
    splitLine: { lineStyle: { color: p.grid, width: 1, type: "solid" } },
    axisLabel: {
      color: p.muted,
      fontSize: 11,
      formatter: (v: number) => (col ? formatValue(v, col, { compact: true }) : String(v)),
    },
    ...extra,
  };
}

export function categoryAxis(ctx: RenderContext, labels: string[], extra: Record<string, unknown> = {}) {
  const p = ctx.palette;
  return {
    type: "category",
    data: labels,
    axisLine: { lineStyle: { color: p.axis, width: 1 } },
    axisTick: { show: false },
    axisLabel: { color: p.muted, fontSize: 11, hideOverlap: true, width: 120, overflow: "truncate" },
    ...extra,
  };
}

export function legend(ctx: RenderContext, names: string[], icon: "roundRect" | "line" | "circle") {
  if (names.length < 2 || ctx.viz.options?.show_legend === false) return undefined;
  return {
    type: "scroll",
    top: 0,
    left: 0,
    // Legends mirror the mark: a short stroke for lines, a small square for bars/areas.
    icon: icon === "line" ? "roundRect" : icon,
    itemWidth: icon === "line" ? 14 : 8,
    itemHeight: icon === "line" ? 3 : 8,
    itemGap: 14,
    textStyle: { color: ctx.palette.ink2, fontSize: 11 },
    pageTextStyle: { color: ctx.palette.muted },
    data: names,
  };
}

/** Label for a dimension value (dates and date parts formatted, nulls named). */
export function dimLabel(value: Scalar, f: BoundField): string {
  return formatValue(value, f.col);
}

export function seriesColor(ctx: RenderContext, scope: string, member: string): string {
  return colorFor(ctx.palette, ctx.colors.slot(scope, member));
}

export const colorScope = (f: BoundField) => `field:${f.fieldId ?? f.col.label}`;

/** Seed the colour registry with a field's members in stable (sorted) order. */
export function seedColors(ctx: RenderContext, f: BoundField, members: string[]) {
  ctx.colors.seed(colorScope(f), [...members].sort((a, b) => a.localeCompare(b, undefined, { numeric: true })));
}

export interface Category {
  key: string;
  label: string;
  rowIndexes: number[];
  tuple: Record<string, Scalar>;
}

/** Distinct categories formed by the given dimension fields, in row order. */
export function categories(ctx: RenderContext, catFields: BoundField[]): Category[] {
  const out = new Map<string, Category>();
  ctx.rows.forEach((row, i) => {
    const values = catFields.map((f) => row[f.idx]);
    const key = JSON.stringify(values);
    let c = out.get(key);
    if (!c) {
      const tuple: Record<string, Scalar> = {};
      catFields.forEach((f, j) => (tuple[f.key] = values[j]));
      c = { key, label: catFields.map((f, j) => dimLabel(values[j], f)).join(" · ") || "All", rowIndexes: [], tuple };
      out.set(key, c);
    }
    c.rowIndexes.push(i);
  });
  return [...out.values()];
}

export function formatFamily(col: ColumnMeta): string {
  return col.format?.type ?? (col.data_type === "integer" ? "number" : "number");
}
