import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { formatNumber, formatValue } from "../format";
import { dims, measures } from "../model";
import type { ComponentProps } from "./types";

/**
 * Stat tile: label · value · optional delta vs the previous period · optional
 * sparkline. When the worksheet has a (time) dimension, the value is the last
 * period and the delta compares it with the one before.
 */
export function KpiTile({ ctx }: ComponentProps) {
  const m = measures(ctx.fields).find((f) => f.channel === "value") ?? measures(ctx.fields)[0];
  if (!m) return <div className="viz-message">Add a measure to show a value</div>;
  const series = dims(ctx.fields)[0];
  const values = ctx.rows.map((r) => r[m.idx]);
  const last = values.length ? values[values.length - 1] : null;
  const prev = series && values.length > 1 ? values[values.length - 2] : null;
  const delta = typeof last === "number" && typeof prev === "number" && prev !== 0 ? (last - prev) / Math.abs(prev) : null;
  const periodLabel = series && ctx.rows.length ? formatValue(ctx.rows[ctx.rows.length - 1][series.idx], series.col) : null;
  const prevLabel = series && ctx.rows.length > 1 ? formatValue(ctx.rows[ctx.rows.length - 2][series.idx], series.col) : null;

  return (
    <div className="viz-kpi">
      <div className="viz-kpi__value" title={formatValue(last, m.col)}>
        {formatValue(last, m.col, { compact: Math.abs(Number(last)) >= 100000 })}
      </div>
      {periodLabel && <div className="viz-kpi__period">{periodLabel}</div>}
      {delta !== null && (
        <div className={`viz-kpi__delta ${delta > 0 ? "is-up" : delta < 0 ? "is-down" : ""}`}>
          {delta > 0 ? <ArrowUpRight aria-hidden /> : delta < 0 ? <ArrowDownRight aria-hidden /> : <Minus aria-hidden />}
          <span>
            {delta > 0 ? "+" : ""}
            {formatNumber(delta, { type: "percent", decimals: 1 })}
          </span>
          <span className="muted">vs {prevLabel}</span>
        </div>
      )}
      {series && values.length > 2 && <Sparkline values={values.map((v) => (typeof v === "number" ? v : null))} color={ctx.palette.series[0]} muted={ctx.palette.axis} />}
    </div>
  );
}

function Sparkline({ values, color, muted }: { values: (number | null)[]; color: string; muted: string }) {
  const w = 120;
  const h = 28;
  const finite = values.filter((v): v is number => v !== null);
  const min = Math.min(...finite);
  const max = Math.max(...finite);
  const span = max - min || 1;
  const pts = values
    .map((v, i) => (v === null ? null : [(i / (values.length - 1)) * w, h - 3 - ((v - min) / span) * (h - 6)]))
    .filter((p): p is number[] => p !== null);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  const lastPt = pts[pts.length - 1];
  return (
    <svg className="viz-kpi__spark" viewBox={`0 0 ${w} ${h}`} width={w} height={h} aria-hidden>
      <path d={d} fill="none" stroke={muted} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      {lastPt && <circle cx={lastPt[0]} cy={lastPt[1]} r={3} fill={color} />}
    </svg>
  );
}
