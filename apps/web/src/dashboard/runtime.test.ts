import { describe, expect, it } from "vitest";
import type { DashboardSpec } from "@/api/types";
import { highlightFor, initialState, isPristine, reducer, worksheetState } from "./runtime";

const spec: DashboardSpec = {
  spec_version: 1,
  title: "T",
  worksheets: [
    { id: "regions", dataset_id: "ds", viz: { type: "bar", fields: [
      { key: "r", role: "dimension", channel: "x", use: { field: "region" } },
      { key: "s", role: "measure", channel: "y", use: { field: "sales" } }] } },
    { id: "trend", dataset_id: "ds", viz: { type: "line", fields: [
      { key: "m", role: "dimension", channel: "x", use: { field: "order_date", time_unit: "month" } }] } },
    { id: "other", dataset_id: "ds2", viz: { type: "kpi", fields: [] } },
  ],
  filters: [
    { id: "seg", dataset_id: "ds", field: "segment", control: "multi_select", default: ["Consumer"] },
    { id: "only_trend", dataset_id: "ds", field: "category", control: "multi_select", applies_to: ["trend"] },
  ],
  parameters: [{ id: "n", name: "N", data_type: "integer", default: 5 }],
  actions: [
    { id: "fa", type: "filter", source: "regions", targets: "all" },
    { id: "ha", type: "highlight", source: "regions", targets: ["trend"] },
  ],
  layout: { columns: 12, row_height: 36, items: [] },
};

describe("dashboard runtime", () => {
  const reduce = reducer(spec);

  it("starts from defaults", () => {
    const s = initialState(spec);
    expect(s.filters.seg.values).toEqual(["Consumer"]);
    expect(s.parameters.n).toBe(5);
    expect(isPristine(spec, s)).toBe(true);
  });

  it("scopes filters and actions to each worksheet", () => {
    let s = initialState(spec);
    s = reduce(s, { type: "filter", id: "only_trend", values: ["Furniture"] });
    s = reduce(s, { type: "select", worksheet: "regions", tuple: { r: "West" }, additive: false });
    const regions = worksheetState(spec, spec.worksheets[0], s);
    expect(Object.keys(regions.filters)).toEqual(["seg"]);
    expect(regions.selections).toEqual([]); // source is not filtered by its own selection
    const trend = worksheetState(spec, spec.worksheets[1], s);
    expect(Object.keys(trend.filters).sort()).toEqual(["only_trend", "seg"]);
    expect(trend.selections).toEqual([{ action_id: "fa", tuples: [{ r: "West" }] }]);
    const other = worksheetState(spec, spec.worksheets[2], s);
    expect(other.filters).toEqual({}); // different dataset
    expect(isPristine(spec, s)).toBe(false);
  });

  it("toggles additive selections and clears", () => {
    let s = initialState(spec);
    s = reduce(s, { type: "select", worksheet: "regions", tuple: { r: "West" }, additive: true });
    s = reduce(s, { type: "select", worksheet: "regions", tuple: { r: "East" }, additive: true });
    expect(s.selectedBy.regions).toHaveLength(2);
    s = reduce(s, { type: "select", worksheet: "regions", tuple: { r: "West" }, additive: true });
    expect(s.selectedBy.regions).toEqual([{ r: "East" }]);
    s = reduce(s, { type: "select", worksheet: "regions", tuple: null, additive: false });
    expect(s.selections).toEqual({});
  });

  it("derives highlight sets by semantic field", () => {
    let s = initialState(spec);
    s = reduce(s, { type: "select", worksheet: "regions", tuple: { r: "West" }, additive: false });
    const h = highlightFor(spec, spec.worksheets[1], s);
    expect(h && [...h.region]).toEqual(["West"]);
    expect(highlightFor(spec, spec.worksheets[0], s)).toBeNull();
  });

  it("ignores clicks on worksheets that are not action sources", () => {
    const s = initialState(spec);
    expect(reduce(s, { type: "select", worksheet: "trend", tuple: { m: "2024-01-01" }, additive: false })).toBe(s);
  });
});
