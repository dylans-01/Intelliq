# Intelliq Dashboard Specification (IDS) v1

The platform-owned, renderer-independent representation of every dashboard.
Natively built and Tableau-imported dashboards use the same format and the same
runtime. Tableau XML is never used at runtime.

- Source of truth: `apps/api/intelliq/spec/dashboard.py` (Pydantic, `extra="forbid"`).
- JSON Schema: [`generated/dashboard.v1.schema.json`](generated/dashboard.v1.schema.json)
  (regenerate with `intelliq export-schema`), also served at `GET /api/meta/dashboard-schema`.
- TypeScript mirror: `apps/web/src/api/types.ts`.

## Shape

```jsonc
{
  "spec_version": 1,
  "title": "Sales Performance",
  "worksheets": [{
    "id": "by_region",
    "title": "Sales by region",
    "dataset_id": "superstore",                // semantic dataset reference
    "viz": {
      "type": "bar",                           // bar|line|area|scatter|pie|heatmap|kpi|table
      "fields": [
        {"key": "region", "role": "dimension", "channel": "x", "use": {"field": "region"}},
        {"key": "sales",  "role": "measure",   "channel": "y", "use": {"field": "sales"}},
        {"key": "pct",    "role": "measure",   "channel": "tooltip",
         "use": {"field": "sales", "table_calc": {"type": "percent_of_total"}}}
      ],
      "sort": [{"key": "sales", "direction": "desc"}],
      "limit": null,
      "options": {"orientation": "vertical", "stacked": false}
    },
    "filters": [{"filter": {"target": {"field": "returned"}, "op": "not_in", "values": ["Yes"], "stage": "context"}}],
    "drill_hierarchy": null
  }],
  "filters":    [{"id": "f_segment", "dataset_id": "superstore", "field": "segment", "control": "multi_select", "applies_to": "all"}],
  "parameters": [{"id": "top_n", "name": "Top N", "data_type": "integer", "default": 10,
                  "allowed": {"type": "range", "min": 1, "max": 50}}],
  "actions":    [{"id": "a1", "type": "filter", "source": "by_region", "targets": "all"}],
  "layout":     {"columns": 12, "row_height": 36,
                 "items": [{"id": "i1", "kind": "worksheet", "ref": "by_region", "x": 0, "y": 0, "w": 6, "h": 8}]},
  "theme":      {"mode": "system", "palette": "intelliq", "density": "comfortable"},
  "provenance": {"source": "tableau", "workbook_name": "sales.twbx", "workbook_sha256": "…",
                 "import_id": "…", "refs": {"by_region": "worksheet:Sales by Region"}}
}
```

## Field use (`use`)
| Property | Meaning |
|---|---|
| `field` / `formula` | A semantic field id, **or** an ad-hoc formula (authors only; stored in the spec) |
| `aggregation` | `sum`, `avg`, `min`, `max`, `count`, `countd`, `median`, `attr`, `stdev`, `var` (measures) |
| `time_unit` | Continuous date truncation: `year`…`minute` (Tableau "green" dates) |
| `date_part` | Discrete date part: `year`, `quarter`, `month`, `week`, `weekday`, `day` ("blue" dates) |
| `table_calc` | Quick table calculation with `partition_by` (output ids) — addressing is the remaining dimensions |
| `label`, `format` | Display overrides |

## Filter stages
Filters declare where they run in Tableau's order of operations:
`datasource → context → (FIXED LOD) → dimension → (INCLUDE/EXCLUDE LOD) → measure → (table calcs) → table_calc`.
`top_n` filters are evaluated after context filters only, as in Tableau.

## Runtime state (viewer input)
Viewers never send fields or formulas. They send only `RuntimeState`:
filter values for declared dashboard filters, parameter values (validated against
`allowed`), mark selections for declared actions, and a drill path. The server
compiles this against the **stored** spec (`spec/compile.py`).

## Versioning and migrations
- Each save creates an immutable version; one version may be published.
- Documents carry `spec_version`. To change the schema: add `MIGRATIONS[n]` in
  `spec/migrations.py` (upgrades version n → n+1), bump `SPEC_VERSION`. Stored
  documents are upgraded on read and re-validated; newer-than-server documents
  are rejected. The migration chain is unit-tested (`tests/unit/test_spec.py`).
