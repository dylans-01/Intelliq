import type { Scalar } from "@/api/types";
import { DataTable, type Column } from "@/design-system";
import { formatValue } from "../format";
import { isDimmed, sameTuple, tupleOf } from "../model";
import type { ComponentProps } from "./types";

/** Table visualization; rows are selectable to drive filter/highlight actions. */
export function TableViz({ ctx, onSelect }: ComponentProps) {
  const columns: Column<Scalar[]>[] = ctx.fields.map((f) => ({
    key: f.key,
    header: f.col.label,
    numeric: f.role === "measure" && f.col.data_type !== "string" && f.col.data_type !== "boolean",
    value: (row) => row[f.idx],
    render: (row) => formatValue(row[f.idx], f.col),
  }));
  const hasDims = ctx.fields.some((f) => f.role === "dimension");
  return (
    <DataTable
      rows={ctx.rows}
      columns={columns}
      rowKey={(_, i) => String(i)}
      pageSize={ctx.viz.options?.page_size ?? 25}
      searchable={ctx.rows.length > 10}
      dense
      onRowClick={hasDims ? (row) => onSelect(tupleOf(row, ctx.fields), false) : undefined}
      isSelected={(row) => ctx.selected.some((s) => sameTuple(s, tupleOf(row, ctx.fields))) || (!!ctx.highlight && !isDimmed(ctx, tupleOf(row, ctx.fields)))}
      caption={ctx.viz.type === "table" ? "Data table" : "Chart data"}
    />
  );
}
