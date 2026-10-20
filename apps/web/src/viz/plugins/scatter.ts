import type { Scalar } from "@/api/types";
import { formatValue } from "../format";
import { DIM_OPACITY, dims, isDimmed, measures, onChannel, tupleOf, type RenderContext } from "../model";
import { baseOption, colorScope, dimLabel, legend, seedColors, seriesColor, tooltipHtml, valueAxis } from "./common";
import type { BuildResult } from "./types";

/** All-pairs palette validation clears only the first three slots; fold the rest. */
const SCATTER_SERIES_CAP = 3;

export function buildScatter(ctx: RenderContext): BuildResult {
  const ms = measures(ctx.fields);
  const x = onChannel(ms, "x")[0] ?? ms[0];
  const y = onChannel(ms, "y")[0] ?? ms.find((m) => m !== x);
  if (!x || !y) return { option: null, message: "Scatter plots need a measure on X and another on Y" };
  const size = onChannel(ms, "size")[0];
  const colorDim = onChannel(dims(ctx.fields), "color")[0];
  const detail = dims(ctx.fields).filter((d) => d !== colorDim);

  let groups: { name: string; color: string; member: Scalar | undefined; rows: number[] }[];
  if (colorDim) {
    const order: Scalar[] = [];
    const byMember = new Map<string, number[]>();
    ctx.rows.forEach((r, i) => {
      const k = String(r[colorDim.idx]);
      if (!byMember.has(k)) {
        byMember.set(k, []);
        order.push(r[colorDim.idx]);
      }
      byMember.get(k)!.push(i);
    });
    seedColors(ctx, colorDim, order.map(String));
    const ranked = [...order].sort((a, b) => byMember.get(String(b))!.length - byMember.get(String(a))!.length);
    const keep = new Set(ranked.slice(0, SCATTER_SERIES_CAP).map(String));
    groups = order.filter((m) => keep.has(String(m))).map((m) => ({
      name: dimLabel(m, colorDim),
      color: seriesColor(ctx, colorScope(colorDim), String(m)),
      member: m,
      rows: byMember.get(String(m))!,
    }));
    const rest = order.filter((m) => !keep.has(String(m)));
    if (rest.length) {
      groups.push({ name: `Other (${rest.length})`, color: ctx.palette.other, member: undefined, rows: rest.flatMap((m) => byMember.get(String(m))!) });
    }
  } else {
    groups = [{ name: y.col.label, color: ctx.palette.series[0], member: undefined, rows: ctx.rows.map((_, i) => i) }];
  }

  const sizeValues = size ? ctx.rows.map((r) => Number(r[size.idx]) || 0) : [];
  const maxSize = Math.max(1, ...sizeValues.map(Math.abs));
  const symbolSize = (i: number) => (size ? 8 + 20 * Math.sqrt(Math.abs(sizeValues[i]) / maxSize) : 9);

  const series = groups.map((g) => ({
    type: "scatter",
    name: g.name,
    color: g.color,
    data: g.rows.map((i) => ({
      value: [ctx.rows[i][x.idx], ctx.rows[i][y.idx]],
      symbolSize: symbolSize(i),
      rowIndex: i,
      itemStyle: isDimmed(ctx, tupleOf(ctx.rows[i], ctx.fields)) ? { opacity: DIM_OPACITY } : undefined,
    })),
    itemStyle: { borderColor: ctx.palette.surface, borderWidth: 2, opacity: 0.9 },
    emphasis: { scale: 1.25 },
  }));

  const option = {
    ...baseOption(ctx),
    legend: legend(ctx, groups.map((g) => g.name), "circle"),
    grid: { left: 8, right: 16, top: groups.length > 1 ? 30 : 12, bottom: 24, containLabel: true },
    xAxis: valueAxis(ctx, x.col, {
      name: x.col.label, nameLocation: "middle", nameGap: 26, scale: true,
      nameTextStyle: { color: ctx.palette.muted, fontSize: 11 },
    }),
    yAxis: valueAxis(ctx, y.col, { scale: true }),
    tooltip: {
      ...baseOption(ctx).tooltip,
      trigger: "item",
      formatter: (p: { data: { rowIndex: number }; color: string }) => {
        const row = ctx.rows[p.data.rowIndex];
        const title = detail.map((d) => dimLabel(row[d.idx], d)).join(" · ") || "Mark";
        const rows = [
          { label: x.col.label, value: formatValue(row[x.idx], x.col) },
          { label: y.col.label, value: formatValue(row[y.idx], y.col) },
          ...(size ? [{ label: size.col.label, value: formatValue(row[size.idx], size.col) }] : []),
          ...(colorDim ? [{ color: p.color, shape: "dot" as const, label: colorDim.col.label, value: dimLabel(row[colorDim.idx], colorDim) }] : []),
        ];
        return tooltipHtml(title, rows);
      },
    },
    series,
  };

  return {
    option,
    pick: (si, di) => {
      const g = groups[si];
      const i = g?.rows[di];
      return i === undefined ? null : { tuple: tupleOf(ctx.rows[i], ctx.fields) };
    },
  };
}
