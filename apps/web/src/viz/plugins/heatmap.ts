import { formatValue } from "../format";
import { dims, isDimmed, measures, onChannel, type RenderContext } from "../model";
import { baseOption, categories, categoryAxis, tooltipHtml } from "./common";
import type { BuildResult } from "./types";

export function buildHeatmap(ctx: RenderContext): BuildResult {
  const ds = dims(ctx.fields);
  const xd = onChannel(ds, "x")[0] ?? ds[0];
  const yd = onChannel(ds, "y")[0] ?? ds.find((d) => d !== xd);
  const m = onChannel(measures(ctx.fields), "color")[0] ?? measures(ctx.fields)[0];
  if (!xd || !yd || !m) return { option: null, message: "Heatmaps need two dimensions and a measure" };

  const xs = categories(ctx, [xd]);
  const ys = categories(ctx, [yd]);
  const xi = new Map(xs.map((c, i) => [c.key, i]));
  const yi = new Map(ys.map((c, i) => [c.key, i]));
  const values = ctx.rows.map((r) => Number(r[m.idx]));
  const finite = values.filter(Number.isFinite);
  const min = Math.min(...finite);
  const max = Math.max(...finite);

  const data = ctx.rows.map((r, i) => ({
    value: [xi.get(JSON.stringify([r[xd.idx]])), yi.get(JSON.stringify([r[yd.idx]])), r[m.idx]],
    rowIndex: i,
    itemStyle: isDimmed(ctx, { [xd.key]: r[xd.idx], [yd.key]: r[yd.idx] }) ? { opacity: 0.25 } : undefined,
  }));

  const option = {
    ...baseOption(ctx),
    grid: { left: 8, right: 16, top: 10, bottom: 44, containLabel: true },
    xAxis: categoryAxis(ctx, xs.map((c) => c.label), { splitArea: { show: false } }),
    yAxis: categoryAxis(ctx, ys.map((c) => c.label), { inverse: true }),
    visualMap: {
      min: Number.isFinite(min) ? min : 0,
      max: Number.isFinite(max) ? max : 1,
      calculable: false,
      orient: "horizontal",
      left: "center",
      bottom: 0,
      itemWidth: 10,
      itemHeight: 120,
      inRange: { color: ctx.palette.seq },
      textStyle: { color: ctx.palette.muted, fontSize: 11 },
      formatter: (v: number) => formatValue(v, m.col, { compact: true }),
    },
    tooltip: {
      ...baseOption(ctx).tooltip,
      trigger: "item",
      formatter: (p: { data: { rowIndex: number } }) => {
        const r = ctx.rows[p.data.rowIndex];
        return tooltipHtml(`${xs[xi.get(JSON.stringify([r[xd.idx]]))!].label} · ${ys[yi.get(JSON.stringify([r[yd.idx]]))!].label}`, [
          { label: m.col.label, value: formatValue(r[m.idx], m.col) },
        ]);
      },
    },
    series: [{ type: "heatmap", data, itemStyle: { borderColor: ctx.palette.surface, borderWidth: 2, borderRadius: 2 } }],
  };
  return {
    option,
    pick: (_si, di) => {
      const r = ctx.rows[data[di]?.rowIndex ?? -1];
      return r ? { tuple: { [xd.key]: r[xd.idx], [yd.key]: r[yd.idx] } } : null;
    },
  };
}
