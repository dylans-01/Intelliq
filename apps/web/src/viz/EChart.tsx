import { BarChart, HeatmapChart, LineChart, PieChart, ScatterChart } from "echarts/charts";
import { AriaComponent, GridComponent, LegendComponent, TooltipComponent, VisualMapComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";

echarts.use([
  BarChart, LineChart, ScatterChart, PieChart, HeatmapChart,
  GridComponent, TooltipComponent, LegendComponent, VisualMapComponent, AriaComponent,
  CanvasRenderer,
]);

export interface ChartEvent {
  seriesIndex: number;
  dataIndex: number;
  componentType: string;
  shiftKey: boolean;
}

/** Thin ECharts host: owns the instance, resizes with its container, forwards clicks. */
export function EChart({
  option,
  onClick,
  onAxisClick,
  ariaLabel,
}: {
  option: Record<string, unknown>;
  onClick?: (e: ChartEvent) => void;
  onAxisClick?: (dataIndex: number, shiftKey: boolean) => void;
  ariaLabel: string;
}) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const handlers = useRef({ onClick, onAxisClick });
  handlers.current = { onClick, onAxisClick };

  useEffect(() => {
    if (!el.current) return;
    const instance = echarts.init(el.current, undefined, { renderer: "canvas" });
    chart.current = instance;
    instance.on("click", (p: unknown) => {
      const e = p as { seriesIndex: number; dataIndex: number; componentType: string; event?: { event?: MouseEvent } };
      handlers.current.onClick?.({
        seriesIndex: e.seriesIndex,
        dataIndex: e.dataIndex,
        componentType: e.componentType,
        shiftKey: !!e.event?.event?.shiftKey,
      });
    });
    // Clicking empty plot area in a category chart selects the whole category.
    instance.getZr().on("click", (e: { target?: unknown; offsetX: number; offsetY: number; event?: MouseEvent }) => {
      if (e.target || !handlers.current.onAxisClick) return;
      const opt = instance.getOption() as { xAxis?: { type?: string }[]; yAxis?: { type?: string }[] };
      const horizontal = opt.yAxis?.[0]?.type === "category";
      if (!horizontal && opt.xAxis?.[0]?.type !== "category") return;
      const point = [e.offsetX, e.offsetY];
      if (!instance.containPixel({ gridIndex: 0 }, point)) return;
      const value = instance.convertFromPixel({ gridIndex: 0 }, point) as number[];
      const idx = horizontal ? value[1] : value[0];
      if (Number.isFinite(idx)) handlers.current.onAxisClick(Math.round(idx), !!e.event?.shiftKey);
    });
    const ro = new ResizeObserver(() => instance.resize());
    ro.observe(el.current);
    return () => {
      ro.disconnect();
      instance.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(option, { notMerge: true, lazyUpdate: true });
  }, [option]);

  return <div ref={el} className="viz-echart" role="img" aria-label={ariaLabel} />;
}
