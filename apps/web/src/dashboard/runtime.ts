/**
 * Dashboard runtime state (pure functions, unit-tested).
 *
 * The viewer's interactive state is: dashboard filter values, parameter
 * values, mark selections per action, and a drill path per worksheet. The
 * server receives only the slice that affects a given worksheet, so changing
 * one filter never refetches worksheets it doesn't apply to.
 */

import type { DashboardSpec, RelativeDate, RuntimeState, Scalar, Worksheet } from "@/api/types";
import type { Highlight, Tuple } from "@/viz/model";

export interface ViewState {
  filters: Record<string, { values: Scalar[]; relative?: RelativeDate | null }>;
  parameters: Record<string, Scalar>;
  /** action id → selected mark tuples (keys are the source worksheet's viz field keys) */
  selections: Record<string, Tuple[]>;
  /** source worksheet id → selected tuples (for drawing the selection) */
  selectedBy: Record<string, Tuple[]>;
  drill: Record<string, Scalar[]>;
}

export function initialState(spec: DashboardSpec): ViewState {
  const filters: ViewState["filters"] = {};
  for (const f of spec.filters) filters[f.id] = { values: f.default ?? [] };
  const parameters: ViewState["parameters"] = {};
  for (const p of spec.parameters) parameters[p.id] = p.default ?? null;
  return { filters, parameters, selections: {}, selectedBy: {}, drill: {} };
}

const applies = (targets: string[] | "all" | undefined, wsId: string) => !targets || targets === "all" || targets.includes(wsId);

export type Action =
  | { type: "filter"; id: string; values: Scalar[]; relative?: RelativeDate | null }
  | { type: "parameter"; id: string; value: Scalar }
  | { type: "select"; worksheet: string; tuple: Tuple | null; additive: boolean }
  | { type: "drill"; worksheet: string; path: Scalar[] }
  | { type: "reset"; spec: DashboardSpec };

export function reducer(spec: DashboardSpec) {
  return (state: ViewState, action: Action): ViewState => {
    switch (action.type) {
      case "filter":
        return { ...state, filters: { ...state.filters, [action.id]: { values: action.values, relative: action.relative } } };
      case "parameter":
        return { ...state, parameters: { ...state.parameters, [action.id]: action.value } };
      case "drill":
        return { ...state, drill: { ...state.drill, [action.worksheet]: action.path } };
      case "reset":
        return initialState(action.spec);
      case "select": {
        const sourced = spec.actions.filter((a) => a.source === action.worksheet && (a.type === "filter" || a.type === "highlight"));
        if (!sourced.length) return state;
        const current = state.selectedBy[action.worksheet] ?? [];
        let next: Tuple[];
        if (action.tuple === null) next = [];
        else if (action.additive) {
          const key = JSON.stringify(action.tuple);
          next = current.some((t) => JSON.stringify(t) === key)
            ? current.filter((t) => JSON.stringify(t) !== key)
            : [...current, action.tuple];
        } else next = [action.tuple];
        const selections = { ...state.selections };
        for (const a of sourced) {
          if (next.length) selections[a.id] = next;
          else delete selections[a.id];
        }
        return { ...state, selections, selectedBy: { ...state.selectedBy, [action.worksheet]: next } };
      }
    }
  };
}

/** The RuntimeState slice the server needs for one worksheet. */
export function worksheetState(spec: DashboardSpec, ws: Worksheet, state: ViewState): RuntimeState {
  const filters: RuntimeState["filters"] = {};
  for (const f of spec.filters) {
    if (f.dataset_id !== ws.dataset_id || !applies(f.applies_to, ws.id)) continue;
    const v = state.filters[f.id];
    if (v) filters[f.id] = v.relative ? { values: v.values, relative: v.relative } : { values: v.values };
  }
  const selections: RuntimeState["selections"] = [];
  for (const a of spec.actions) {
    if (a.type !== "filter" || a.source === ws.id || !applies(a.targets, ws.id)) continue;
    const tuples = state.selections[a.id];
    if (tuples?.length) selections.push({ action_id: a.id, tuples });
  }
  return { filters, parameters: state.parameters, selections, drill: state.drill[ws.id] ?? [] };
}

/** Client-side highlight for a target worksheet from highlight actions. */
export function highlightFor(spec: DashboardSpec, ws: Worksheet, state: ViewState): Highlight | null {
  let out: Highlight | null = null;
  for (const a of spec.actions) {
    if (a.type !== "highlight" || a.source === ws.id || !applies(a.targets, ws.id)) continue;
    const tuples = state.selections[a.id];
    if (!tuples?.length) continue;
    const source = spec.worksheets.find((w) => w.id === a.source);
    if (!source) continue;
    for (const f of source.viz.fields) {
      if (f.role !== "dimension" || !f.use.field || f.use.time_unit || f.use.date_part) continue;
      if (a.fields?.length && !a.fields.includes(f.use.field)) continue;
      const values = tuples.filter((t) => f.key in t).map((t) => String(t[f.key]));
      if (!values.length) continue;
      out = out ?? {};
      out[f.use.field] = new Set(values);
    }
  }
  return out;
}

export function isPristine(spec: DashboardSpec, state: ViewState): boolean {
  return JSON.stringify(initialState(spec)) === JSON.stringify(state);
}
