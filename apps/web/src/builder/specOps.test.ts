import { describe, expect, it } from "vitest";
import type { SemanticField } from "@/api/types";
import { addAction, addField, addFilter, addText, addWorksheet, applyLayout, changeType, emptySpec, removeItem, uniqueId } from "./specOps";

const region: SemanticField = { id: "region", name: "Region", role: "dimension", data_type: "string" };
const date: SemanticField = { id: "order_date", name: "Order Date", role: "dimension", data_type: "date" };
const sales: SemanticField = { id: "sales", name: "Sales", role: "measure", data_type: "real" };
const profit: SemanticField = { id: "profit", name: "Profit", role: "measure", data_type: "real" };

describe("spec operations", () => {
  it("adds worksheets on a packed grid", () => {
    let s = emptySpec();
    s = addWorksheet(s, "ds", "bar").spec;
    s = addWorksheet(s, "ds", "line").spec;
    s = addWorksheet(s, "ds", "table").spec;
    const [a, b, c] = s.layout.items;
    expect([a.x, a.y, b.x, b.y]).toEqual([0, 0, 6, 0]); // two halves share a row
    expect([c.x, c.y, c.w]).toEqual([0, 8, 12]); // full-width table on the next row
    expect(new Set(s.worksheets.map((w) => w.id)).size).toBe(3);
  });

  it("places fields onto channels by role and capacity", () => {
    let { spec: s, id } = addWorksheet(emptySpec(), "ds", "bar");
    let ws = s.worksheets.find((w) => w.id === id)!;
    ws = addField(ws, region);
    ws = addField(ws, sales);
    ws = addField(ws, profit);
    expect(ws.viz.fields.map((f) => [f.key, f.channel])).toEqual([["region", "x"], ["sales", "y"], ["profit", "y"]]);
    expect(ws.title).toBe("Sales");
    ws = addField(ws, date); // x is full (max 1) → next dimension channel
    expect(ws.viz.fields[3].channel).toBe("color");
    expect(ws.viz.fields[3].use.time_unit).toBe("month");
    s = { ...s, worksheets: [ws] };
    expect(s.worksheets[0].viz.fields).toHaveLength(4);
  });

  it("re-maps channels when the chart type changes", () => {
    const { spec, id } = addWorksheet(emptySpec(), "ds", "bar");
    let ws = spec.worksheets.find((w) => w.id === id)!;
    ws = addField(addField(addField(ws, region), sales), profit);
    const scatter = changeType(ws, "scatter");
    expect(scatter.viz.fields.map((f) => f.channel)).toEqual(["x", "y", "detail"]);
    const table = changeType(ws, "table");
    expect(table.viz.fields.every((f) => f.channel === "column")).toBe(true);
    const kpi = changeType(ws, "kpi");
    expect(kpi.viz.fields.map((f) => f.channel)).toEqual(["x", "value"]);
  });

  it("removes items with their dependents", () => {
    let s = emptySpec();
    const a = addWorksheet(s, "ds");
    s = a.spec;
    const b = addWorksheet(s, "ds");
    s = b.spec;
    s = addAction(s, "filter", a.id);
    s = addFilter(s, "ds", region).spec;
    s = { ...s, filters: s.filters.map((f) => ({ ...f, applies_to: [a.id, b.id] })) };
    s = removeItem(s, `i_${a.id}`);
    expect(s.worksheets.map((w) => w.id)).toEqual([b.id]);
    expect(s.actions).toEqual([]);
    expect(s.filters[0].applies_to).toEqual([b.id]);
  });

  it("applies grid layout changes and adds text", () => {
    let s = addWorksheet(emptySpec(), "ds").spec;
    s = addText(s).spec;
    const ws = s.layout.items[0];
    s = applyLayout(s, [{ i: ws.id, x: 2, y: 4, w: 5, h: 6 }]);
    expect(s.layout.items[0]).toMatchObject({ x: 2, y: 4, w: 5, h: 6 });
    expect(s.layout.items[1].kind).toBe("text");
  });

  it("generates valid unique ids", () => {
    expect(uniqueId("Sales by Region!", [])).toBe("sales_by_region");
    expect(uniqueId("sales", ["sales"])).toBe("sales_2");
    expect(uniqueId("2024 totals", [])).toBe("w_2024_totals");
  });
});
