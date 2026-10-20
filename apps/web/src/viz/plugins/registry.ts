import type { VizSpec, VizType } from "@/api/types";
import { buildCartesian } from "./cartesian";
import { buildHeatmap } from "./heatmap";
import { KpiTile } from "./Kpi";
import { buildPie } from "./pie";
import { buildScatter } from "./scatter";
import { TableViz } from "./TableViz";
import type { ChartPlugin } from "./types";

const count = (viz: VizSpec, role: "dimension" | "measure") => viz.fields.filter((f) => f.role === role).length;

const needs =
  (dimsMin: number, measuresMin: number, message: string) =>
  (viz: VizSpec): string | null =>
    count(viz, "dimension") >= dimsMin && count(viz, "measure") >= measuresMin ? null : message;

/**
 * Chart type registry. Adding a chart type = adding an entry here; the
 * dashboard runtime, builder and renderer discover it from this list.
 */
export const PLUGINS: ChartPlugin[] = [
  {
    type: "bar",
    label: "Bar",
    description: "Compare values across categories",
    channels: [
      { channel: "x", label: "Category", roles: ["dimension"], max: 1, required: true },
      { channel: "y", label: "Value", roles: ["measure"], required: true },
      { channel: "color", label: "Color", roles: ["dimension"], max: 1 },
      { channel: "detail", label: "Detail", roles: ["dimension"] },
      { channel: "tooltip", label: "Tooltip", roles: ["measure"] },
    ],
    build: (ctx) => buildCartesian(ctx, "bar"),
    ready: needs(1, 1, "Add a category and a measure"),
  },
  {
    type: "line",
    label: "Line",
    description: "Show change over time",
    channels: [
      { channel: "x", label: "X axis", roles: ["dimension"], max: 1, required: true, hint: "Usually a date" },
      { channel: "y", label: "Value", roles: ["measure"], required: true },
      { channel: "color", label: "Color", roles: ["dimension"], max: 1 },
      { channel: "tooltip", label: "Tooltip", roles: ["measure"] },
    ],
    build: (ctx) => buildCartesian(ctx, "line"),
    ready: needs(1, 1, "Add an X axis field and a measure"),
  },
  {
    type: "area",
    label: "Area",
    description: "Show volume over time",
    channels: [
      { channel: "x", label: "X axis", roles: ["dimension"], max: 1, required: true },
      { channel: "y", label: "Value", roles: ["measure"], required: true },
      { channel: "color", label: "Color", roles: ["dimension"], max: 1 },
    ],
    build: (ctx) => buildCartesian(ctx, "area"),
    ready: needs(1, 1, "Add an X axis field and a measure"),
  },
  {
    type: "scatter",
    label: "Scatter",
    description: "Relate two measures",
    channels: [
      { channel: "x", label: "X value", roles: ["measure"], max: 1, required: true },
      { channel: "y", label: "Y value", roles: ["measure"], max: 1, required: true },
      { channel: "detail", label: "Detail", roles: ["dimension"], hint: "One point per member" },
      { channel: "color", label: "Color", roles: ["dimension"], max: 1, hint: "Up to 3 groups" },
      { channel: "size", label: "Size", roles: ["measure"], max: 1 },
    ],
    build: buildScatter,
    ready: needs(0, 2, "Add measures to X and Y"),
  },
  {
    type: "pie",
    label: "Donut",
    description: "Part-to-whole for a few categories",
    channels: [
      { channel: "color", label: "Slices", roles: ["dimension"], max: 1, required: true },
      { channel: "y", label: "Value", roles: ["measure"], max: 1, required: true },
    ],
    build: buildPie,
    ready: needs(1, 1, "Add a dimension and a measure"),
  },
  {
    type: "heatmap",
    label: "Heatmap",
    description: "Magnitude across two dimensions",
    channels: [
      { channel: "x", label: "Columns", roles: ["dimension"], max: 1, required: true },
      { channel: "y", label: "Rows", roles: ["dimension"], max: 1, required: true },
      { channel: "color", label: "Color", roles: ["measure"], max: 1, required: true },
    ],
    build: buildHeatmap,
    ready: needs(2, 1, "Add two dimensions and a measure"),
  },
  {
    type: "kpi",
    label: "KPI",
    description: "A single headline number",
    channels: [
      { channel: "value", label: "Value", roles: ["measure"], max: 1, required: true },
      { channel: "x", label: "Trend by", roles: ["dimension"], max: 1, hint: "Adds a sparkline and delta" },
    ],
    component: KpiTile,
    ready: needs(0, 1, "Add a measure"),
  },
  {
    type: "table",
    label: "Table",
    description: "Detailed rows and columns",
    channels: [{ channel: "column", label: "Columns", roles: ["dimension", "measure"], required: true }],
    component: TableViz,
    ready: (viz) => (viz.fields.length ? null : "Add fields to show columns"),
  },
];

export function plugin(type: VizType): ChartPlugin {
  return PLUGINS.find((p) => p.type === type) ?? PLUGINS[PLUGINS.length - 1];
}
