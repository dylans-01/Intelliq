/**
 * Bar, line and area charts.
 *
 * - One categorical axis built from the x dimension (+ detail dimensions).
 * - Series come from a colour dimension, or from multiple measures.
 * - Measures with different formats (e.g. currency and percent) are drawn as
 *   vertically stacked small multiples sharing the category axis — never as a
 *   dual-axis chart.
 */

import type { Scalar } from "@/api/types";
import { formatValue } from "../format";
import { DIM_OPACITY, dims, isDimmed, measures, onChannel, type BoundField, type RenderContext } from "../model";
import {
  baseOption,
  categories,
  categoryAxis,
  colorScope,
  dimLabel,
  formatFamily,
  legend,
  seedColors,
  seriesColor,
  tooltipHtml,
  valueAxis,
  type Category,
  type TooltipRow,
} from "./common";
import type { BuildResult, PickResult } from "./types";

type Kind = "bar" | "line" | "area";

interface SeriesDef {
  name: string;
  color: string;
  measure: BoundField;
  member?: Scalar;
  values: (number | null)[];
  rowIdx: (number | null)[];
}

export function buildCartesian(ctx: RenderContext, kind: Kind): BuildResult {
  const opts = ctx.viz.options ?? {};
  const horizontal = kind === "bar" && opts.orientation === "horizontal";
  const stacked = !!opts.stacked;
  const xDims = onChannel(dims(ctx.fields), "x", "y", "detail");
  const colorDim = onChannel(dims(ctx.fields), "color")[0];
  const seriesMeasures = measures(ctx.fields).filter((m) => m.channel !== "tooltip" && m.channel !== "label");
  const tooltipMeasures = measures(ctx.fields).filter((m) => m.channel === "tooltip");
  if (seriesMeasures.length === 0) return { option: null, message: "Add a measure to plot" };

  const cats = categories(ctx, xDims);
  const catIndex = new Map(cats.map((c, i) => [c.key, i]));
  const catKeyOf = (rowI: number) => JSON.stringify(xDims.map((f) => ctx.rows[rowI][f.idx]));

  // Group measures by format family → one panel per family (small multiples).
  const panels: BoundField[][] = [];
  for (const m of seriesMeasures) {
    const family = formatFamily(m.col);
    const panel = panels.find((p) => formatFamily(p[0].col) === family);
    if (panel && !colorDim) panel.push(m);
    else panels.push([m]);
  }

  const series: SeriesDef[][] = panels.map((panel) => {
    if (colorDim) {
      const members: Scalar[] = [];
      const seen = new Set<string>();
      ctx.rows.forEach((r) => {
        const k = JSON.stringify(r[colorDim.idx]);
        if (!seen.has(k)) {
          seen.add(k);
          members.push(r[colorDim.idx]);
        }
      });
      seedColors(ctx, colorDim, members.map((m) => String(m)));
      const m = panel[0];
      return members.map((member) => {
        const values: (number | null)[] = new Array(cats.length).fill(null);
        const rowIdx: (number | null)[] = new Array(cats.length).fill(null);
        ctx.rows.forEach((r, i) => {
          if (r[colorDim.idx] !== member) return;
          const ci = catIndex.get(catKeyOf(i))!;
          values[ci] = r[m.idx] as number | null;
          rowIdx[ci] = i;
        });
        return { name: dimLabel(member, colorDim), color: seriesColor(ctx, colorScope(colorDim), String(member)), measure: m, member, values, rowIdx };
      });
    }
    return panel.map((m) => {
      const values: (number | null)[] = new Array(cats.length).fill(null);
      const rowIdx: (number | null)[] = new Array(cats.length).fill(null);
      cats.forEach((c, ci) => {
        const i = c.rowIndexes[0];
        values[ci] = ctx.rows[i][m.idx] as number | null;
        rowIdx[ci] = i;
      });
      const scope = `measures:${ctx.viz.fields.filter((f) => f.role === "measure").map((f) => f.key).join(",")}`;
      return { name: m.col.label, color: seriesColor(ctx, scope, m.key), measure: m, values, rowIdx };
    });
  });

  const allSeries = series.flat();
  const labels = cats.map((c) => c.label);
  const n = panels.length;
  const legendNames = allSeries.length > 1 && (colorDim || n === 1) ? [...new Set(allSeries.map((s) => s.name))] : [];
  // Room on the right for end labels when lines are directly labelled (measured, not clipped).
  const endLabels = kind !== "bar" && allSeries.length >= 1 && allSeries.length <= 4;
  const longest = allSeries.length === 1 ? 7 : Math.max(...allSeries.map((s) => s.name.length));
  const right = endLabels ? Math.min(160, 16 + Math.ceil(longest * 6.4)) : 16;
  const grids =
    n === 1
      ? [{ left: 8, right, top: legendNames.length ? 30 : 10, bottom: 8, containLabel: true }]
      : panels.map((_, i) => {
          // Small multiples: equal vertical slices, each leaving room for its axis name.
          const slice = 100 / n;
          return { left: 8, right, top: `${i * slice + 6}%`, height: `${slice - 14}%`, containLabel: true };
        });

  const catAxes = panels.map((_, i) =>
    categoryAxis(ctx, labels, {
      gridIndex: i,
      inverse: horizontal,
      boundaryGap: kind === "bar",
      axisLabel: {
        ...categoryAxis(ctx, labels).axisLabel,
        show: horizontal || i === n - 1,
      },
    }),
  );
  const valAxes = panels.map((panel, i) =>
    valueAxis(ctx, panel[0].col, {
      gridIndex: i,
      name: n > 1 ? panel.map((m) => m.col.label).join(", ") : undefined,
      nameLocation: "end",
      nameTextStyle: { color: ctx.palette.muted, fontSize: 11, align: "left" },
    }),
  );

  const echSeries = series.flatMap((panelSeries, pi) =>
    panelSeries.map((s, si) => {
      const data = s.values.map((v, ci) => {
        const tuple = cats[ci].tuple;
        const full = colorDim ? { ...tuple, [colorDim.key]: s.member ?? null } : tuple;
        const dim = isDimmed(ctx, full);
        return { value: v, itemStyle: dim ? { opacity: DIM_OPACITY } : undefined };
      });
      const common = {
        name: s.name,
        xAxisIndex: pi,
        yAxisIndex: pi,
        data,
        color: s.color,
        emphasis: { focus: "none" as const },
        stack: stacked ? `stack-${pi}` : undefined,
      };
      if (kind === "bar") {
        // Only the outermost segment of a stack gets the rounded data-end.
        const isTop = !stacked || si === panelSeries.length - 1;
        const radius = isTop ? (horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]) : 0;
        return {
          ...common,
          type: "bar",
          barMaxWidth: 24,
          barGap: "18%",
          barCategoryGap: "32%",
          itemStyle: { borderRadius: radius, borderColor: ctx.palette.surface, borderWidth: stacked ? 1 : 0 },
          label: opts.show_labels
            ? {
                show: true,
                position: horizontal ? "right" : "top",
                color: ctx.palette.ink2,
                fontSize: 11,
                formatter: (p: { value: number | null }) => (p.value == null ? "" : formatValue(p.value, s.measure.col, { compact: true })),
              }
            : undefined,
        };
      }
      const showEnd = panelSeries.length >= 1 && panelSeries.length <= 4;
      return {
        ...common,
        type: "line",
        smooth: opts.smooth ? 0.25 : false,
        connectNulls: false,
        showSymbol: !!opts.show_points || cats.length === 1,
        symbol: "circle",
        symbolSize: 8,
        lineStyle: { width: 2, cap: "round", join: "round" },
        itemStyle: { borderColor: ctx.palette.surface, borderWidth: 2 },
        areaStyle: kind === "area" ? { opacity: stacked ? 0.32 : 0.1 } : undefined,
        endLabel: showEnd
          ? {
              show: true,
              color: ctx.palette.ink2,
              fontSize: 11,
              distance: 6,
              formatter: (p: { value: number | null; seriesName: string }) =>
                panelSeries.length === 1 ? (p.value == null ? "" : formatValue(p.value, s.measure.col, { compact: true })) : p.seriesName,
            }
          : undefined,
      };
    }),
  );

  const tooltipFor = (ci: number): string => {
    const cat: Category = cats[ci];
    const rows: TooltipRow[] = [];
    for (const s of allSeries) {
      const v = s.values[ci];
      if (v === null && colorDim) continue;
      rows.push({ color: s.color, shape: kind === "bar" ? "rect" : "line", label: s.name, value: formatValue(v, s.measure.col) });
    }
    if (!colorDim) {
      const ri = cat.rowIndexes[0];
      for (const t of tooltipMeasures) rows.push({ label: t.col.label, value: formatValue(ctx.rows[ri][t.idx], t.col) });
    }
    return tooltipHtml(cat.label, rows);
  };

  const option = {
    ...baseOption(ctx),
    legend: legend(ctx, legendNames, kind === "bar" ? "roundRect" : "line"),
    grid: grids,
    xAxis: horizontal ? valAxes : catAxes,
    yAxis: horizontal ? catAxes : valAxes,
    tooltip: {
      ...baseOption(ctx).tooltip,
      trigger: "axis",
      axisPointer:
        kind === "bar"
          ? { type: "shadow", shadowStyle: { color: ctx.palette.grid, opacity: 0.45 } }
          : { type: "line", lineStyle: { color: ctx.palette.axis, width: 1 } },
      formatter: (params: { dataIndex: number }[] | { dataIndex: number }) => {
        const p = Array.isArray(params) ? params[0] : params;
        return p ? tooltipFor(p.dataIndex) : "";
      },
    },
    series: echSeries,
  };

  const pick = (seriesIndex: number, dataIndex: number): PickResult | null => {
    const s = allSeries[seriesIndex];
    const cat = cats[dataIndex];
    if (!s || !cat) return null;
    const tuple = colorDim ? { ...cat.tuple, [colorDim.key]: s.member ?? null } : { ...cat.tuple };
    return { tuple };
  };

  return { option, pick, axisPick: (dataIndex) => (cats[dataIndex] ? { tuple: { ...cats[dataIndex].tuple } } : null) };
}
