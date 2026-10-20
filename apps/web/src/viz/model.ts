import type { Channel, ColumnMeta, QueryResult, Scalar, VizField, VizSpec } from "@/api/types";
import type { ChartPalette, ColorRegistry } from "./colors";

/** A viz field bound to its result column. */
export interface BoundField {
  key: string;
  role: "dimension" | "measure";
  channel: Channel;
  col: ColumnMeta;
  idx: number;
  fieldId: string | null;
}

export type Tuple = Record<string, Scalar>; // viz field key → value

/** Highlight state from a highlight action: semantic field id → highlighted values. */
export type Highlight = Record<string, Set<string>>;

export interface RenderContext {
  viz: VizSpec;
  fields: BoundField[];
  rows: Scalar[][];
  palette: ChartPalette;
  colors: ColorRegistry;
  /** Marks the viewer selected in this worksheet (filter/highlight action source). */
  selected: Tuple[];
  highlight: Highlight | null;
}

export function bind(viz: VizSpec, result: QueryResult): BoundField[] {
  const keyMap = result.key_map ?? {};
  const out: BoundField[] = [];
  viz.fields.forEach((f: VizField) => {
    const colId = keyMap[f.key];
    const idx = result.columns.findIndex((c) => c.id === colId);
    if (idx < 0) return;
    const col = result.columns[idx];
    out.push({ key: f.key, role: f.role, channel: f.channel, col, idx, fieldId: col.field_id ?? f.use.field ?? null });
  });
  return out;
}

export const onChannel = (fields: BoundField[], ...channels: Channel[]) => fields.filter((f) => channels.includes(f.channel));
export const dims = (fields: BoundField[]) => fields.filter((f) => f.role === "dimension");
export const measures = (fields: BoundField[]) => fields.filter((f) => f.role === "measure");

export function tupleOf(row: Scalar[], fields: BoundField[]): Tuple {
  const t: Tuple = {};
  for (const f of dims(fields)) t[f.key] = row[f.idx];
  return t;
}

export const sameTuple = (a: Tuple, b: Tuple) => {
  const ka = Object.keys(a);
  return ka.length === Object.keys(b).length && ka.every((k) => a[k] === b[k]);
};

/** Should a mark be drawn de-emphasised given selection and highlight state? */
export function isDimmed(ctx: RenderContext, tuple: Tuple): boolean {
  if (ctx.selected.length > 0) {
    const hit = ctx.selected.some((s) => Object.keys(s).every((k) => !(k in tuple) || s[k] === tuple[k]));
    if (!hit) return true;
  }
  if (ctx.highlight) {
    for (const [fieldId, values] of Object.entries(ctx.highlight)) {
      const f = ctx.fields.find((b) => b.role === "dimension" && b.fieldId === fieldId && b.key in tuple);
      if (f && !values.has(String(tuple[f.key]))) return true;
    }
  }
  return false;
}

export const DIM_OPACITY = 0.22;
