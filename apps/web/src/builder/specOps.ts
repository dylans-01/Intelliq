/**
 * Pure operations on a dashboard spec used by the builder. Keeping them pure
 * makes the editor's behaviour testable and gives undo/redo for free.
 */

import type {
  Action,
  Channel,
  DashboardFilter,
  DashboardSpec,
  FieldUse,
  LayoutItem,
  Parameter,
  SemanticField,
  VizField,
  VizType,
  Worksheet,
} from "@/api/types";
import { plugin } from "@/viz/plugins/registry";

export function emptySpec(title = "Untitled dashboard"): DashboardSpec {
  return { spec_version: 1, title, description: "", worksheets: [], filters: [], parameters: [], actions: [], layout: { columns: 12, row_height: 36, items: [] } };
}

export function uniqueId(base: string, taken: Iterable<string>): string {
  const used = new Set(taken);
  const clean = base.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40) || "item";
  const start = /^[a-z]/.test(clean) ? clean : `w_${clean}`;
  let id = start;
  let i = 2;
  while (used.has(id)) id = `${start}_${i++}`;
  return id;
}

const nextY = (spec: DashboardSpec) => spec.layout.items.reduce((m, i) => Math.max(m, i.y + i.h), 0);

function place(spec: DashboardSpec, item: Omit<LayoutItem, "x" | "y">): LayoutItem {
  // Fill the last row if there's room, otherwise start a new row.
  const bottom = nextY(spec);
  const lastRow = spec.layout.items.filter((i) => i.y + i.h === bottom);
  const usedX = lastRow.reduce((m, i) => Math.max(m, i.x + i.w), 0);
  const rowTop = lastRow.length ? Math.min(...lastRow.map((i) => i.y)) : bottom;
  if (lastRow.length && usedX + item.w <= spec.layout.columns && lastRow.every((i) => i.h === item.h)) {
    return { ...item, x: usedX, y: rowTop };
  }
  return { ...item, x: 0, y: bottom };
}

const DEFAULT_SIZE: Record<VizType, [number, number]> = {
  kpi: [3, 3], bar: [6, 8], line: [6, 8], area: [6, 8], scatter: [6, 8], pie: [4, 8], heatmap: [6, 8], table: [12, 8],
};

export function addWorksheet(spec: DashboardSpec, datasetId: string, type: VizType = "bar"): { spec: DashboardSpec; id: string } {
  const id = uniqueId(`${type}_chart`, [...spec.worksheets.map((w) => w.id), ...spec.layout.items.map((i) => i.id)]);
  const ws: Worksheet = { id, title: "New visualization", dataset_id: datasetId, viz: { type, fields: [], options: {} }, filters: [] };
  const [w, h] = DEFAULT_SIZE[type];
  const item = place(spec, { id: `i_${id}`, kind: "worksheet", ref: id, w, h });
  return { spec: { ...spec, worksheets: [...spec.worksheets, ws], layout: { ...spec.layout, items: [...spec.layout.items, item] } }, id };
}

export function addFilter(spec: DashboardSpec, datasetId: string, field: SemanticField): { spec: DashboardSpec; id: string } {
  const id = uniqueId(`filter_${field.id}`, spec.filters.map((f) => f.id));
  const control: DashboardFilter["control"] =
    field.data_type === "date" || field.data_type === "datetime" ? "date_range" : field.role === "measure" ? "range" : "multi_select";
  const filter: DashboardFilter = { id, label: field.name, dataset_id: datasetId, field: field.id, control, default: [], applies_to: "all" };
  const item = place(spec, { id: `i_${id}`, kind: "filter", ref: id, w: 3, h: 2 });
  return { spec: { ...spec, filters: [...spec.filters, filter], layout: { ...spec.layout, items: [...spec.layout.items, item] } }, id };
}

export function addText(spec: DashboardSpec, text = "Add a note"): { spec: DashboardSpec; id: string } {
  const id = uniqueId("text", spec.layout.items.map((i) => i.id));
  const item = place(spec, { id, kind: "text", text, w: 12, h: 2 });
  return { spec: { ...spec, layout: { ...spec.layout, items: [...spec.layout.items, item] } }, id };
}

export function addParameter(spec: DashboardSpec, p: Omit<Parameter, "id">): { spec: DashboardSpec; id: string } {
  const id = uniqueId(p.name, spec.parameters.map((x) => x.id));
  const item = place(spec, { id: `i_${id}`, kind: "parameter", ref: id, w: 3, h: 2 });
  return { spec: { ...spec, parameters: [...spec.parameters, { ...p, id }], layout: { ...spec.layout, items: [...spec.layout.items, item] } }, id };
}

/** Remove a layout item and whatever it references (worksheet/filter/parameter) plus dangling actions. */
export function removeItem(spec: DashboardSpec, itemId: string): DashboardSpec {
  const item = spec.layout.items.find((i) => i.id === itemId);
  if (!item) return spec;
  const items = spec.layout.items.filter((i) => i.id !== itemId);
  let { worksheets, filters, parameters, actions } = spec;
  if (item.kind === "worksheet") {
    worksheets = worksheets.filter((w) => w.id !== item.ref);
    actions = actions
      .filter((a) => a.source !== item.ref)
      .map((a) => (Array.isArray(a.targets) ? { ...a, targets: a.targets.filter((t) => t !== item.ref) } : a));
    filters = filters.map((f) => (Array.isArray(f.applies_to) ? { ...f, applies_to: f.applies_to.filter((t) => t !== item.ref) } : f));
  }
  if (item.kind === "filter") filters = filters.filter((f) => f.id !== item.ref);
  if (item.kind === "parameter") parameters = parameters.filter((p) => p.id !== item.ref);
  return { ...spec, worksheets, filters, parameters, actions, layout: { ...spec.layout, items } };
}

export function updateWorksheet(spec: DashboardSpec, id: string, fn: (w: Worksheet) => Worksheet): DashboardSpec {
  return { ...spec, worksheets: spec.worksheets.map((w) => (w.id === id ? fn(w) : w)) };
}

export function defaultUse(field: SemanticField): FieldUse {
  if (field.role === "dimension" && (field.data_type === "date" || field.data_type === "datetime")) return { field: field.id, time_unit: "month" };
  return { field: field.id };
}

/** Pick the channel a newly added field should land on for this chart type. */
export function channelFor(ws: Worksheet, role: "dimension" | "measure"): Channel | null {
  const p = plugin(ws.viz.type);
  for (const c of p.channels) {
    if (!c.roles.includes(role)) continue;
    const used = ws.viz.fields.filter((f) => f.channel === c.channel).length;
    if (c.max === undefined || used < c.max) return c.channel;
  }
  return null;
}

export function addField(ws: Worksheet, field: SemanticField, channel?: Channel): Worksheet {
  const role = field.role;
  const target = channel ?? channelFor(ws, role);
  if (!target) return ws;
  const key = uniqueId(field.id, ws.viz.fields.map((f) => f.key));
  const vf: VizField = { key, role, channel: target, use: defaultUse(field) };
  return { ...ws, title: ws.title === "New visualization" || !ws.title ? autoTitle(ws, field) : ws.title, viz: { ...ws.viz, fields: [...ws.viz.fields, vf] } };
}

export function addFormulaField(ws: Worksheet, formula: string, label: string, role: "dimension" | "measure"): Worksheet {
  const target = channelFor(ws, role);
  if (!target) return ws;
  const key = uniqueId(label || "calc", ws.viz.fields.map((f) => f.key));
  return { ...ws, viz: { ...ws.viz, fields: [...ws.viz.fields, { key, role, channel: target, use: { formula, label } }] } };
}

function autoTitle(ws: Worksheet, field: SemanticField): string {
  const measures = ws.viz.fields.filter((f) => f.role === "measure");
  if (field.role === "measure" && !measures.length) return field.name;
  return ws.title || field.name;
}

export function removeField(ws: Worksheet, key: string): Worksheet {
  return { ...ws, viz: { ...ws.viz, fields: ws.viz.fields.filter((f) => f.key !== key), sort: (ws.viz.sort ?? []).filter((s) => s.key !== key) } };
}

export function updateField(ws: Worksheet, key: string, use: Partial<FieldUse>, channel?: Channel): Worksheet {
  return {
    ...ws,
    viz: { ...ws.viz, fields: ws.viz.fields.map((f) => (f.key === key ? { ...f, channel: channel ?? f.channel, use: { ...f.use, ...use } } : f)) },
  };
}

/** Change chart type, moving fields onto the new type's channels by role. */
export function changeType(ws: Worksheet, type: VizType): Worksheet {
  const p = plugin(type);
  let next: Worksheet = { ...ws, viz: { ...ws.viz, type, fields: [] } };
  // Measures first for scatter (x then y), otherwise keep original order.
  const ordered = [...ws.viz.fields].sort((a, b) => (type === "scatter" ? (a.role === b.role ? 0 : a.role === "measure" ? -1 : 1) : 0));
  // Channels only carry over between chart families that share a layout.
  const cartesian = ["bar", "line", "area"];
  const sameFamily = cartesian.includes(ws.viz.type) && cartesian.includes(type);
  for (const f of ordered) {
    const preferred = sameFamily ? p.channels.find((c) => c.channel === f.channel && c.roles.includes(f.role)) : undefined;
    const used = preferred ? next.viz.fields.filter((x) => x.channel === preferred.channel).length : 0;
    const channel = preferred && (preferred.max === undefined || used < preferred.max) ? preferred.channel : channelFor(next, f.role);
    if (channel) next = { ...next, viz: { ...next.viz, fields: [...next.viz.fields, { ...f, channel }] } };
  }
  return next;
}

export function applyLayout(spec: DashboardSpec, layout: { i: string; x: number; y: number; w: number; h: number }[]): DashboardSpec {
  const byId = new Map(layout.map((l) => [l.i, l]));
  return {
    ...spec,
    layout: {
      ...spec.layout,
      items: spec.layout.items.map((it) => {
        const l = byId.get(it.id);
        return l ? { ...it, x: l.x, y: l.y, w: l.w, h: l.h } : it;
      }),
    },
  };
}

export function addAction(spec: DashboardSpec, type: "filter" | "highlight", source: string): DashboardSpec {
  const id = uniqueId(`${type}_${source}`, spec.actions.map((a) => a.id));
  const sourceWs = spec.worksheets.find((w) => w.id === source);
  const action: Action = { id, name: `${type === "filter" ? "Filter" : "Highlight"} from ${sourceWs?.title || source}`, type, source, targets: "all" };
  return { ...spec, actions: [...spec.actions, action] };
}
