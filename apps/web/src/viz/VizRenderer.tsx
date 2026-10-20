import { useMemo } from "react";
import type { QueryResult, VizSpec } from "@/api/types";
import { useResolvedMode } from "@/lib/theme";
import { readPalette, type ColorRegistry } from "./colors";
import { EChart } from "./EChart";
import { bind, type Highlight, type RenderContext, type Tuple } from "./model";
import { plugin } from "./plugins/registry";
import "./viz.css";

export interface VizRendererProps {
  viz: VizSpec;
  result: QueryResult;
  colors: ColorRegistry;
  selected?: Tuple[];
  highlight?: Highlight | null;
  /** tuple = clicked mark's dimension values; null = clear. additive = shift-click. */
  onSelect?: (tuple: Tuple | null, additive: boolean) => void;
  title?: string;
}

/**
 * Renders a visualization from its spec and query result. Contains no data
 * access and no business rules — only presentation and mark → tuple mapping.
 */
const NO_SELECTION: Tuple[] = []; // stable default so memoised chart options aren't rebuilt each render

export function VizRenderer({ viz, result, colors, selected = NO_SELECTION, highlight = null, onSelect, title }: VizRendererProps) {
  const mode = useResolvedMode();
  // Palette is read from CSS variables, so it must be re-read whenever the theme changes.
  const palette = useMemo(() => readPalette(), [mode]);
  const p = plugin(viz.type);
  const ctx: RenderContext = useMemo(
    () => ({ viz, fields: bind(viz, result), rows: result.rows, palette, colors, selected, highlight }),
    [viz, result, palette, colors, selected, highlight],
  );
  const built = useMemo(() => (p.build ? p.build(ctx) : null), [p, ctx]);
  const select = onSelect ?? (() => {});

  if (result.rows.length === 0) {
    return <div className="viz-message">No data for the current filters</div>;
  }
  if (p.component) {
    const Component = p.component;
    return <Component ctx={ctx} result={result} onSelect={select} />;
  }
  if (!built?.option) {
    return <div className="viz-message">{built?.message ?? "This chart is not configured yet"}</div>;
  }
  return (
    <EChart
      option={built.option}
      ariaLabel={`${p.label} chart${title ? `: ${title}` : ""}. Use the table view for exact values.`}
      onClick={(e) => {
        const picked = built.pick?.(e.seriesIndex, e.dataIndex);
        if (!picked) return;
        const already = selected.some((s) => JSON.stringify(s) === JSON.stringify(picked.tuple));
        select(already && !e.shiftKey ? null : picked.tuple, e.shiftKey);
      }}
      onAxisClick={
        built.axisPick
          ? (i, shift) => {
              const picked = built.axisPick?.(i);
              if (picked) select(picked.tuple, shift);
            }
          : undefined
      }
    />
  );
}
