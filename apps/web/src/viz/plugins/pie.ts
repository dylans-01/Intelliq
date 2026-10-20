import { formatNumber, formatValue } from "../format";
import { DIM_OPACITY, dims, isDimmed, measures, type RenderContext } from "../model";
import { baseOption, colorScope, dimLabel, legend, seedColors, seriesColor, tooltipHtml } from "./common";
import type { BuildResult } from "./types";

/** Part-to-whole reads at a glance only for a handful of slices. */
const MAX_SLICES = 6;

export function buildPie(ctx: RenderContext): BuildResult {
  const dim = dims(ctx.fields).find((d) => d.channel === "color") ?? dims(ctx.fields)[0];
  const m = measures(ctx.fields).find((x) => x.channel !== "tooltip" && x.channel !== "label") ?? measures(ctx.fields)[0];
  if (!dim || !m) return { option: null, message: "Pie charts need one dimension and one measure" };

  const slices = ctx.rows
    .map((r, i) => ({ member: r[dim.idx], value: Number(r[m.idx]) || 0, rowIndex: i }))
    .filter((s) => s.value > 0);
  seedColors(ctx, dim, slices.map((s) => String(s.member)));
  slices.sort((a, b) => b.value - a.value);
  const head = slices.slice(0, slices.length > MAX_SLICES ? MAX_SLICES - 1 : MAX_SLICES);
  const tail = slices.slice(head.length);
  const total = slices.reduce((s, x) => s + x.value, 0) || 1;

  const data = head.map((s) => ({
    name: dimLabel(s.member, dim),
    value: s.value,
    rowIndex: s.rowIndex,
    itemStyle: {
      color: seriesColor(ctx, colorScope(dim), String(s.member)),
      opacity: isDimmed(ctx, { [dim.key]: s.member }) ? DIM_OPACITY : 1,
    },
  }));
  if (tail.length) {
    data.push({
      name: `Other (${tail.length})`,
      value: tail.reduce((s, x) => s + x.value, 0),
      rowIndex: -1,
      itemStyle: { color: ctx.palette.other, opacity: 1 },
    });
  }
  const donut = ctx.viz.options?.donut !== false;

  const option = {
    ...baseOption(ctx),
    legend: legend(ctx, data.map((d) => d.name), "roundRect"),
    tooltip: {
      ...baseOption(ctx).tooltip,
      trigger: "item",
      formatter: (p: { name: string; value: number; color: string }) =>
        tooltipHtml(p.name, [
          { color: p.color, label: m.col.label, value: formatValue(p.value, m.col) },
          { label: "Share", value: formatNumber(p.value / total, { type: "percent", decimals: 1 }) },
        ]),
    },
    series: [
      {
        type: "pie",
        radius: donut ? ["50%", "74%"] : [0, "74%"],
        center: ["50%", data.length > 1 ? "55%" : "50%"],
        avoidLabelOverlap: true,
        itemStyle: { borderColor: ctx.palette.surface, borderWidth: 2, borderRadius: 3 },
        label: { show: false },
        emphasis: { scale: true, scaleSize: 3 },
        data,
      },
    ],
  };
  return {
    option,
    pick: (_si, di) => {
      const d = data[di];
      if (!d || d.rowIndex < 0) return null;
      return { tuple: { [dim.key]: ctx.rows[d.rowIndex][dim.idx] } };
    },
  };
}
