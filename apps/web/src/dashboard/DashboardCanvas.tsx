import { useMemo, useReducer, type CSSProperties } from "react";
import type { DashboardSpec, LayoutItem } from "@/api/types";
import { ColorRegistry } from "@/viz/colors";
import { FilterControl, ParameterControl } from "./Controls";
import { highlightFor, initialState, reducer, worksheetState, type Action, type ViewState } from "./runtime";
import { TextTile, WorksheetTile } from "./WorksheetTile";
import "./dashboard.css";

export const ROW_PX = 36;
export const GAP_PX = 12;

export function useDashboardRuntime(spec: DashboardSpec) {
  const reduce = useMemo(() => reducer(spec), [spec]);
  return useReducer(reduce, spec, initialState);
}

/**
 * Renders a dashboard spec: placed filter/parameter controls, worksheets and
 * text on a 12-column grid. On narrow screens items stack in reading order.
 */
export function DashboardCanvas({
  spec,
  dashboardId,
  draft,
  state,
  dispatch,
  preview = false,
  previewImportId,
  drillLevels = {},
}: {
  spec: DashboardSpec;
  dashboardId?: string;
  draft: boolean;
  state: ViewState;
  dispatch: (a: Action) => void;
  preview?: boolean;
  /** Import whose pending calculated fields the preview should overlay. */
  previewImportId?: string;
  /** worksheet id → hierarchy level field ids (from the dashboard API, or the builder's datasets) */
  drillLevels?: Record<string, string[]>;
}) {
  // One registry per dashboard: a member keeps its colour across every worksheet.
  const colors = useMemo(() => new ColorRegistry(), [dashboardId]);
  const placedFilters = new Set(spec.layout.items.filter((i) => i.kind === "filter").map((i) => i.ref));
  const placedParams = new Set(spec.layout.items.filter((i) => i.kind === "parameter").map((i) => i.ref));
  const looseFilters = spec.filters.filter((f) => !placedFilters.has(f.id));
  const looseParams = spec.parameters.filter((p) => !placedParams.has(p.id));

  const items = [...spec.layout.items].sort((a, b) => a.y - b.y || a.x - b.x);
  const columns = spec.layout.columns;

  const renderItem = (item: LayoutItem) => {
    if (item.kind === "worksheet") {
      const ws = spec.worksheets.find((w) => w.id === item.ref);
      if (!ws) return null;
      return (
        <WorksheetTile
          ws={ws}
          spec={spec}
          dashboardId={dashboardId}
          draft={draft}
          state={worksheetState(spec, ws, state)}
          colors={colors}
          selected={state.selectedBy[ws.id] ?? NONE}
          highlight={highlightFor(spec, ws, state)}
          onSelect={(tuple, additive) => dispatch({ type: "select", worksheet: ws.id, tuple, additive })}
          drillPath={state.drill[ws.id] ?? NONE}
          onDrill={(path) => dispatch({ type: "drill", worksheet: ws.id, path })}
          hierarchyLevels={drillLevels[ws.id]}
          preview={preview}
          previewImportId={previewImportId}
        />
      );
    }
    if (item.kind === "filter") {
      const f = spec.filters.find((x) => x.id === item.ref);
      if (!f) return null;
      return (
        <FilterControl
          filter={f}
          dashboardId={dashboardId}
          draft={draft}
          values={state.filters[f.id]?.values ?? []}
          onChange={(values) => dispatch({ type: "filter", id: f.id, values })}
        />
      );
    }
    if (item.kind === "parameter") {
      const p = spec.parameters.find((x) => x.id === item.ref);
      if (!p) return null;
      return <ParameterControl parameter={p} value={state.parameters[p.id] ?? null} onChange={(value) => dispatch({ type: "parameter", id: p.id, value })} />;
    }
    return <TextTile text={item.text ?? ""} />;
  };

  return (
    <div className="dash-canvas">
      {(looseFilters.length > 0 || looseParams.length > 0) && (
        <div className="dash-filterbar" role="group" aria-label="Filters">
          {looseFilters.map((f) => (
            <FilterControl
              key={f.id}
              filter={f}
              dashboardId={dashboardId}
              draft={draft}
              values={state.filters[f.id]?.values ?? []}
              onChange={(values) => dispatch({ type: "filter", id: f.id, values })}
            />
          ))}
          {looseParams.map((p) => (
            <ParameterControl key={p.id} parameter={p} value={state.parameters[p.id] ?? null} onChange={(value) => dispatch({ type: "parameter", id: p.id, value })} />
          ))}
        </div>
      )}
      <div className="dash-grid" style={{ "--cols": columns, "--row": `${ROW_PX}px`, "--gap": `${GAP_PX}px` } as CSSProperties}>
        {items.map((item) => (
          <div
            key={item.id}
            className={`dash-cell dash-cell--${item.kind}`}
            style={
              {
                "--x": item.x + 1,
                "--w": item.w,
                "--y": item.y + 1,
                "--h": item.h,
              } as CSSProperties
            }
          >
            {renderItem(item)}
          </div>
        ))}
      </div>
    </div>
  );
}

const NONE: never[] = [];

