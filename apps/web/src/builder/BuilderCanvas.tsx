import { GripVertical, Trash2 } from "lucide-react";
import { useMemo } from "react";
import GridLayout, { WidthProvider, type Layout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import type { DashboardSpec, LayoutItem } from "@/api/types";
import { FilterControl, ParameterControl } from "@/dashboard/Controls";
import { GAP_PX, ROW_PX } from "@/dashboard/DashboardCanvas";
import { highlightFor, worksheetState, type Action, type ViewState } from "@/dashboard/runtime";
import { TextTile, WorksheetTile } from "@/dashboard/WorksheetTile";
import { IconButton } from "@/design-system";
import type { ColorRegistry } from "@/viz/colors";
import { plugin } from "@/viz/plugins/registry";

const Grid = WidthProvider(GridLayout);
const NONE: never[] = [];

/** Editable grid: drag by the handle, resize from the corner, click to select. */
export function BuilderCanvas({
  spec,
  selected,
  onSelect,
  onLayout,
  onRemove,
  state,
  dispatch,
  colors,
  drillLevels,
}: {
  spec: DashboardSpec;
  selected: string | null;
  onSelect: (itemId: string | null) => void;
  onLayout: (layout: Layout[]) => void;
  onRemove: (itemId: string) => void;
  state: ViewState;
  dispatch: (a: Action) => void;
  colors: ColorRegistry;
  drillLevels: Record<string, string[]>;
}) {
  const layout = useMemo(
    () => spec.layout.items.map((i) => ({ i: i.id, x: i.x, y: i.y, w: i.w, h: i.h, minW: 2, minH: i.kind === "worksheet" ? 3 : 2 })),
    [spec.layout.items],
  );

  const body = (item: LayoutItem) => {
    if (item.kind === "worksheet") {
      const ws = spec.worksheets.find((w) => w.id === item.ref);
      if (!ws) return null;
      const missing = plugin(ws.viz.type).ready(ws.viz);
      if (missing)
        return (
          <div className="bld-placeholder">
            <strong>{ws.title || "New visualization"}</strong>
            <span>{missing}. Select this tile and add fields from the left panel.</span>
          </div>
        );
      return (
        <WorksheetTile
          ws={ws}
          spec={spec}
          draft
          preview
          state={worksheetState(spec, ws, state)}
          colors={colors}
          selected={state.selectedBy[ws.id] ?? NONE}
          highlight={highlightFor(spec, ws, state)}
          onSelect={(tuple, additive) => dispatch({ type: "select", worksheet: ws.id, tuple, additive })}
          drillPath={state.drill[ws.id] ?? NONE}
          onDrill={(path) => dispatch({ type: "drill", worksheet: ws.id, path })}
          hierarchyLevels={drillLevels[ws.id]}
        />
      );
    }
    if (item.kind === "filter") {
      const f = spec.filters.find((x) => x.id === item.ref);
      return f ? <FilterControl filter={f} draft values={state.filters[f.id]?.values ?? []} onChange={(values) => dispatch({ type: "filter", id: f.id, values })} /> : null;
    }
    if (item.kind === "parameter") {
      const p = spec.parameters.find((x) => x.id === item.ref);
      return p ? <ParameterControl parameter={p} value={state.parameters[p.id] ?? p.default ?? null} onChange={(value) => dispatch({ type: "parameter", id: p.id, value })} /> : null;
    }
    return <TextTile text={item.text ?? ""} />;
  };

  return (
    <div className="bld-canvas" onMouseDown={(e) => e.target === e.currentTarget && onSelect(null)}>
      {spec.layout.items.length === 0 && (
        <div className="bld-canvas__empty">
          <strong>Start with a visualization</strong>
          <span>Use “Add chart” in the toolbar, then add fields from the left panel.</span>
        </div>
      )}
      <Grid
        layout={layout}
        cols={spec.layout.columns}
        rowHeight={ROW_PX}
        margin={[GAP_PX, GAP_PX]}
        containerPadding={[0, 0]}
        draggableHandle=".bld-item__handle"
        // No auto-compaction: the editor must preserve the authored layout exactly
        // (and opening a dashboard must not create phantom unsaved changes).
        compactType={null}
        preventCollision
        onLayoutChange={onLayout}
      >
        {spec.layout.items.map((item) => (
          <div
            key={item.id}
            className={`bld-item bld-item--${item.kind}${selected === item.id ? " is-selected" : ""}`}
            onMouseDown={() => onSelect(item.id)}
            onFocus={() => onSelect(item.id)}
            tabIndex={0}
            aria-label={`${item.kind} ${item.ref ?? item.id}`}
          >
            <div className="bld-item__bar">
              <span className="bld-item__handle" aria-label="Drag to move" title="Drag to move">
                <GripVertical />
              </span>
              <IconButton
                size="sm"
                label="Remove from dashboard"
                icon={<Trash2 />}
                onMouseDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation();
                  onRemove(item.id);
                }}
              />
            </div>
            <div className="bld-item__body">{body(item)}</div>
          </div>
        ))}
      </Grid>
    </div>
  );
}
