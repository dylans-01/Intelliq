# Architecture

Intelliq is a modular monorepo. Each subsystem has an explicit contract and can be
replaced without touching the others. Nothing depends on Tableau at runtime.

```
apps/
  api/                 Python package `intelliq` (FastAPI) — all server modules
    intelliq/
      calc/            formula parser, typed AST, type/level checker, SQL compiler, table-calc evaluator
      semantic/        dataset model (fields, metrics, joins, hierarchies, RLS), resolver, lineage
      query/           request contract, planner, executor, result cache
      connectors/      connector contract + DuckDB, PostgreSQL, SQL Server, Snowflake, Databricks
      spec/            Intelliq Dashboard Specification (IDS) v1, migrations, runtime-state compiler
      importer/        safe .twb/.twbx reading, workbook model, conversion + compatibility report
      platform/        metadata store, auth/sessions, RBAC, secrets, rate limits, bootstrap
      validation/      reference comparison and parity case runner
      api/             HTTP routes (thin): authn/z, validation, audit, error mapping
    tests/             unit, integration (oracle SQL, API, contract), fixtures
  web/                 React + TypeScript (Vite)
    src/
      design-system/   tokens (light/dark), primitives, states
      viz/             visualization engine: plugin registry, renderers, formatting, colours
      dashboard/       runtime: state, filters/parameters/actions, tiles, canvas
      builder/         editing experience: spec operations, shelves, inspector, grid editor
      pages/ shell/ api/ auth/ lib/
tests/e2e/             Playwright browser tests
docs/                  this documentation; docs/generated/ is produced from code
```

The brief suggested separate `packages/*` and `services/*` directories. They were
consolidated into one Python package and one web app because, at this stage,
they share a process and a release cycle; the module boundaries are enforced by
imports (e.g. the renderer cannot import connectors; the planner never imports
FastAPI) rather than by repository layout. Splitting into deployable services
is possible later without code changes to the modules themselves.

## Request flow: a viewer opens a dashboard

```mermaid
sequenceDiagram
  participant B as Browser (viewer)
  participant A as API (dashboards route)
  participant S as spec.compile
  participant P as query.planner
  participant X as query.executor
  participant C as Connector
  B->>A: POST /dashboards/{id}/worksheets/{ws}/data {state}
  A->>A: session + CSRF + workspace + capability check
  A->>A: load PUBLISHED spec version (viewers never get drafts)
  A->>S: compile_worksheet(spec, ws, runtime state, dataset)
  S-->>A: QueryRequest (authored fields + validated viewer state)
  A->>P: plan(request, dataset, user context with RLS attributes)
  P-->>A: parameterised SQL template + post-processing plan
  A->>X: execute (cache key = rendered SQL + params + dataset version)
  X->>C: execute(sql, bound params, timeout, row cap, cancel token)
  C-->>X: rows
  X-->>A: table calcs, table-calc filters, limits applied
  A->>A: audit "query.run"
  A-->>B: columns + rows (+ SQL only for authors)
  B->>B: VizRenderer(plugin) draws chart; table twin available
```

## Module contracts

| Module | Input | Output | Must not |
|---|---|---|---|
| `calc` | formula text | typed AST, SQL expressions, table-calc results | know about datasets, HTTP or connectors |
| `semantic` | dataset definition | resolved/typed fields, lineage, validation problems | execute queries |
| `query.planner` | `QueryRequest`, dataset, user context | `PlannedQuery` (SQL template, bound values, result-stage plan) | open connections; concatenate user values into SQL |
| `query.executor` | `PlannedQuery`, connector | `QueryResult` | interpret business rules |
| `connectors` | rendered SQL + positional params | raw rows | accept SQL from users; log credentials |
| `spec` | IDS document + runtime state | validated dashboard, `QueryRequest` | render or query |
| `importer` | workbook bytes + mapping | IDS documents, new semantic fields, report | execute anything in the workbook |
| `viz` (web) | VizSpec + QueryResult | DOM/canvas | fetch data; apply business rules |
| `dashboard` (web) | IDS + viewer state | requests per worksheet, highlights | enforce security (server does) |

## Key decisions
See `11-decisions-and-gaps.md` for the decision log. The most consequential:

1. **Formula language = Tableau syntax subset** parsed into our own AST, so imported
   calculations translate structurally rather than by string replacement.
2. **sqlglot for dialect generation**, with Tableau semantics enforced by our
   compiler (division, truncation, week start, NULL rules) rather than relying on
   each engine's defaults.
3. **Table calculations run on the result set** in Python, separate from
   database-executed aggregates — mirroring Tableau's model and keeping
   addressing/partitioning explicit.
4. **One runtime for native and imported dashboards** — the importer only emits
   IDS documents and semantic fields.
5. **Viewers never send query structure** — only runtime state validated
   against the stored, published spec.
