// Types mirroring the backend contracts (apps/api/intelliq/spec, query, semantic).

export type DataType = "integer" | "real" | "string" | "boolean" | "date" | "datetime";
export type Scalar = string | number | boolean | null;
export type Aggregation = "sum" | "avg" | "min" | "max" | "count" | "countd" | "median" | "attr" | "stdev" | "var";
export type TimeUnit = "year" | "quarter" | "month" | "week" | "day" | "hour" | "minute";
export type DatePart = "year" | "quarter" | "month" | "week" | "weekday" | "day";
export type QuickTableCalcType =
  | "running_total"
  | "percent_of_total"
  | "difference"
  | "percent_difference"
  | "rank"
  | "rank_dense"
  | "index";

export interface Format {
  type?: "number" | "currency" | "percent" | "date" | "text";
  decimals?: number | null;
  currency?: string;
  compact?: boolean;
  date_pattern?: string | null;
}

export interface FieldUse {
  field?: string | null;
  formula?: string | null;
  aggregation?: Aggregation | "none" | null;
  time_unit?: TimeUnit | null;
  date_part?: DatePart | null;
  table_calc?: { type: QuickTableCalcType; partition_by?: string[]; direction?: "asc" | "desc" } | null;
  label?: string | null;
  format?: Format | null;
}

export type FilterOp =
  | "in" | "not_in" | "eq" | "neq" | "gt" | "gte" | "lt" | "lte" | "between"
  | "contains" | "starts_with" | "is_null" | "not_null" | "relative_date" | "top_n";

export interface RelativeDate {
  mode: "current" | "previous" | "last_n" | "to_date";
  unit: "year" | "quarter" | "month" | "week" | "day";
  n: number;
  anchor?: string | null;
}

export interface QueryFilter {
  target?: FieldUse | null;
  column?: string | null;
  op: FilterOp;
  values?: Scalar[];
  stage?: "datasource" | "context" | "dimension" | "measure" | "table_calc";
  relative?: RelativeDate | null;
  top_n?: { n: number; by: FieldUse; direction?: "top" | "bottom" } | null;
}

export type VizType = "bar" | "line" | "area" | "scatter" | "pie" | "table" | "kpi" | "heatmap";
export type Channel = "x" | "y" | "color" | "size" | "label" | "detail" | "tooltip" | "column" | "value";

export interface VizField {
  key: string;
  role: "dimension" | "measure";
  channel: Channel;
  use: FieldUse;
}

export interface VizOptions {
  orientation?: "vertical" | "horizontal";
  stacked?: boolean;
  show_labels?: boolean;
  show_legend?: boolean;
  smooth?: boolean;
  show_points?: boolean;
  donut?: boolean;
  comparison?: "none" | "previous_row";
  page_size?: number;
  palette?: string | null;
}

export interface VizSpec {
  type: VizType;
  fields: VizField[];
  sort?: { key: string; direction: "asc" | "desc" }[];
  limit?: number | null;
  options?: VizOptions;
}

export interface Worksheet {
  id: string;
  title?: string;
  subtitle?: string;
  show_title?: boolean;
  dataset_id: string;
  viz: VizSpec;
  filters?: { filter: QueryFilter }[];
  drill_hierarchy?: string | null;
}

export interface DashboardFilter {
  id: string;
  label?: string;
  dataset_id: string;
  field: string;
  control: "multi_select" | "single_select" | "range" | "date_range" | "relative_date" | "search";
  stage?: "context" | "dimension";
  default?: Scalar[];
  applies_to?: string[] | "all";
}

export interface Parameter {
  id: string;
  name: string;
  data_type: DataType;
  default?: Scalar;
  allowed?: { type: "all" | "list" | "range"; values?: Scalar[]; min?: number | null; max?: number | null; step?: number | null };
  control?: "dropdown" | "slider" | "input";
}

export interface Action {
  id: string;
  name?: string;
  type: "filter" | "highlight" | "navigate";
  source: string;
  targets?: string[] | "all";
  fields?: string[];
  target_dashboard?: string | null;
}

export interface LayoutItem {
  id: string;
  kind: "worksheet" | "filter" | "parameter" | "text";
  ref?: string | null;
  text?: string | null;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface DashboardSpec {
  spec_version: 1;
  title: string;
  description?: string;
  worksheets: Worksheet[];
  filters: DashboardFilter[];
  parameters: Parameter[];
  actions: Action[];
  layout: { columns: 12 | 24; row_height: number; items: LayoutItem[] };
  theme?: { mode: "system" | "light" | "dark"; palette: string; density: "comfortable" | "compact" };
  provenance?: {
    source: "native" | "tableau";
    workbook_name?: string | null;
    workbook_sha256?: string | null;
    import_id?: string | null;
    imported_at?: string | null;
    refs?: Record<string, string>;
  };
}

export interface DashboardSummary {
  id: string;
  title: string;
  status: "draft" | "published";
  current_version: number;
  published_version: number | null;
  source: "native" | "tableau";
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface DashboardDetail extends DashboardSummary {
  version: number;
  spec: DashboardSpec;
  /** worksheet id → hierarchy level field ids, for drill-down */
  drill_levels?: Record<string, string[]>;
}

export interface ColumnMeta {
  id: string;
  label: string;
  role: "dimension" | "measure";
  data_type: string;
  field_id?: string | null;
  format?: Format | null;
  time_unit?: string | null;
  date_part?: string | null;
  table_calc?: boolean;
}

export interface QueryResult {
  columns: ColumnMeta[];
  rows: Scalar[][];
  row_count: number;
  truncated: boolean;
  cache_hit: boolean;
  duration_ms: number;
  query_id: string;
  sql?: string | null;
  key_map?: Record<string, string>;
}

export interface RuntimeState {
  filters: Record<string, { values: Scalar[]; relative?: RelativeDate | null }>;
  parameters: Record<string, Scalar>;
  selections: { action_id: string; tuples: Record<string, Scalar>[] }[];
  drill: Scalar[];
}

export interface SemanticField {
  id: string;
  name: string;
  role: "dimension" | "measure";
  data_type: DataType;
  column?: string | null;
  formula?: string | null;
  default_aggregation?: Aggregation | null;
  format?: Format | null;
  description?: string;
  folder?: string | null;
  hidden?: boolean;
  certified?: boolean;
  owner?: string | null;
  level?: string;
  depends_on?: string[];
  used_by?: string[];
  columns?: string[];
}

export interface DatasetSummary {
  id: string;
  name: string;
  description: string;
  version: number;
  connection_id: string;
  updated_at: string;
  field_count: number;
}

export interface DatasetDetail extends DatasetSummary {
  model: Record<string, unknown> & { fields: SemanticField[] };
  fields: SemanticField[];
  problems: { field: string; message: string }[];
  hierarchies: { id: string; name: string; levels: string[] }[];
}

export interface Me {
  user: { id: string; email: string; name: string };
  workspace: { id: string; role: "admin" | "author" | "viewer" };
  workspaces: { id: string; name: string; role: string }[];
  capabilities: string[];
  csrf_token: string;
}

export interface ConnectionInfo {
  id: string;
  name: string;
  kind: string;
  config: Record<string, unknown>;
  has_secret: boolean;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface ConnectorType {
  kind: string;
  label: string;
  dialect: string;
  verified: boolean;
  fields: { name: string; label: string; secret: boolean; required: boolean }[];
}

export type ReportStatus = "supported" | "adjusted" | "manual" | "unsupported";

export interface ImportReportItem {
  kind: string;
  ref: string;
  status: ReportStatus;
  message: string;
  formula?: string;
  translated?: string;
  source?: string;
  target?: string;
}

export interface ImportPayload {
  id: string;
  filename: string;
  sha256: string;
  size: number;
  status: "analyzed" | "applied";
  mapping: { datasources: Record<string, string>; fields: Record<string, Record<string, string>> };
  dashboard_ids: string[];
  created_at: string;
  report: {
    summary: Record<ReportStatus, number> & { fidelity: number };
    items: ImportReportItem[];
  };
  inventory: {
    datasources: {
      name: string;
      caption: string;
      connection: Record<string, string | null> | null;
      extract: boolean;
      custom_sql: boolean;
      fields: string[];
      raw_fields: { name: string; caption: string }[];
    }[];
    worksheets: string[];
    dashboards: string[];
    parameters: string[];
    actions: string[];
    assets: { path: string; size: number; kind: string }[];
    version: string | null;
  };
  preview?: { dashboards: DashboardSpec[]; new_fields: Record<string, SemanticField[]> };
}

export interface ImportSummary {
  id: string;
  filename: string;
  size: number;
  status: string;
  summary: Partial<Record<ReportStatus, number>> & { fidelity?: number };
  dashboard_ids: string[];
  created_at: string;
}

export interface UserRow {
  id: string;
  email: string;
  name: string;
  role: string;
  is_active: boolean;
  attributes: Record<string, unknown>;
  created_at: string;
  last_login_at: string | null;
}

export interface AuditEntry {
  id: number;
  ts: string;
  actor_id: string | null;
  actor_email: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  detail: Record<string, unknown>;
  ip: string | null;
}

export interface CalcValidation {
  ok: boolean;
  data_type?: string;
  level?: string;
  role?: "dimension" | "measure";
  dependencies?: string[];
  canonical?: string;
  error?: { code: string; message: string; start: number | null; end: number | null };
}
