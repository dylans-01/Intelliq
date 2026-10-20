# Decisions and known gaps

## Decision log
| # | Decision | Alternatives considered | Rationale |
|---|---|---|---|
| D1 | Build fresh in the empty `Intelliq` repository; treat `dianoic` as reference only | Fork Dianoic | Different domain (SMB CSV + AI), no SQL/semantic/query layers; UI issues are structural |
| D2 | Python (FastAPI, Pydantic, sqlglot) backend; React + TypeScript (Vite) frontend | Node/TS backend; Next.js | Mature DB drivers for all targets, sqlglot dialect generation, Pydantic for the spec; SPA keeps the API the only security boundary |
| D3 | One Python package with strict module boundaries instead of many services | Microservices per subsystem | Same process and release cycle today; avoids premature infrastructure (brief §10/11) |
| D4 | Tableau-compatible formula syntax parsed into our own typed AST | Custom syntax; string translation | Imported calculations translate structurally; no string manipulation |
| D5 | Table calculations evaluated on the result set | SQL window functions | Mirrors Tableau's model (addressing/partitioning over marks), avoids dialect differences |
| D6 | `AGG({LOD})` aggregates over distinct (view ∪ LOD) dimensions; LODs in row-level expressions join at row level | Always row-level join | Matches Tableau's documented replicate/aggregate behaviour; row-level join overcounts `SUM({EXCLUDE…})` |
| D7 | `TOTAL()` of non-additive aggregates computed as an EXCLUDE LOD in SQL | Re-aggregate view values | Exact for AVG/COUNTD/MEDIAN |
| D8 | All user values bound via per-query unguessable tokens → driver positional markers | Inline escaped literals | No injection surface; one mechanism for all dialects (`%` escaping for psycopg) |
| D9 | Viewers send runtime state only; spec compiled server-side | Client builds queries | Viewers cannot request fields, formulas or remove filters |
| D10 | ECharts (tree-shaken) behind a plugin registry; KPI/table as React components | Vega-Lite, visx, D3 | Mature interaction/canvas performance; plugin boundary keeps it replaceable |
| D11 | Validated data-viz palette; colour follows entity across a dashboard; no dual axes | Library defaults | Accessibility (CVD-validated) and consistency |
| D12 | SQLite (WAL) metadata store behind a narrow interface | PostgreSQL from day one | Zero-dependency start; interface designed for a PostgreSQL implementation |
| D13 | Local accounts + server-side sessions + CSRF | JWT in local storage | Revocable sessions, HttpOnly cookies, no token exposure to scripts |
| D14 | Synthetic Superstore-shaped sample warehouse, labelled synthetic | Bundle Tableau's sample data | Licensing; never used to claim parity |
| D15 | Imported calculations become dataset fields only when parameter-free | Store all in worksheets | One canonical definition per metric; datasets stay usable outside the dashboard |

## Known gaps (prioritised)
1. **No Tableau parity evidence yet.** Needs real workbooks and reference exports
   (see `07-validation.md`). Importer tested only on a hand-authored workbook.
2. **SQL Server, Snowflake, Databricks adapters not run against live services** —
   SQL generation is tested; run the contract suite with credentials before use.
3. **Metadata store is SQLite** (single node). PostgreSQL implementation of `Store`
   is needed for HA.
4. **No SSO/MFA**, no per-dashboard ACLs, no column-level security.
5. **Scheduling, background jobs, subscriptions, PDF/image export** — not started
   (brief §2F lists scheduling and exports; only CSV export exists).
6. **Navigation actions** are modelled in the spec but not wired in the UI; cross-dashboard
   filter actions are not supported.
7. **Visualization coverage**: no maps, Gantt, box plots, dual-axis (deliberately — small
   multiples instead), reference lines/bands, trend lines, forecasting, annotations.
8. **Calculation coverage**: no sets, groups, bins, regex, spatial, `SCRIPT_*`, `WINDOW_*`
   offsets, `PREVIOUS_VALUE`, `DATENAME`, `ISDATE`, string `SPLIT`; ATTR `*` display.
9. **Table-calc addressing** beyond Table + explicit partitions is approximated.
10. **Builder**: no drag between shelves, no field reorder within a shelf, no formatting
    panel for number formats beyond display names, no dashboard-level theme editor.
11. **Accessibility**: no formal WCAG 2.2 audit yet.
12. **Observability**: no metrics/tracing endpoints.
13. **Multi-worker latency artefact** under uvicorn `--workers` (see `10-operations.md`).
14. Semantic-layer editing UI covers calculated fields; joins, hierarchies and RLS rules are
    edited through the API/JSON for now.
15. **Saved views** (per-user named filter/parameter states) are not built; runtime state
    resets when a dashboard is reopened.
