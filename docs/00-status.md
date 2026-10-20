# Status report

_As of 2026-10-20. Every number on this page comes from a run in this environment on
that date; nothing is extrapolated. "Not verified" means exactly that._

## Test results (latest run)

| Suite | Command | Result |
|---|---|---|
| Backend unit + integration | `cd apps/api && pytest` with `INTELLIQ_TEST_POSTGRES` set | **341 passed, 39 skipped**, 0 failed (43 s) |
| — of which connector contract, DuckDB + PostgreSQL 16.2 | `tests/integration/test_connector_contract.py` | 26 passed (13 per engine) |
| — of which connector contract, SQL Server / Snowflake / Databricks | same | **39 skipped** — no credentials or instances available |
| Lint | `ruff check intelliq tests scripts` | clean |
| Frontend type check | `npm run typecheck` | clean |
| Frontend unit | `npm test` (Vitest) | 17 passed |
| Production build | `npm run build` | ok (bundle sizes in `10-operations.md`) |
| End-to-end (Playwright, Chromium) | `npm run e2e` | **5 passed**: cross-filter + reset, dashboard filters, viewer cannot reach authoring, author builds and saves a chart, Tableau import → report → draft dashboard |
| Calculation support matrix | `scripts/gen_compat_matrix.py` | 78 functions, every one with at least one executable test case |

Backend test inventory (380 collected): function compatibility 123, connector contract 65
(13 × 5 engines), typecheck 42, API 28 (incl. authz/RLS/CSRF/export), query engine 30,
dialects 28, parser 20, importer 17, spec 12, table calcs 10, comparison 4, validation
runner 1.

## Phase reports

### Phase 1 — Audit
- **Implemented:** `01-audit.md`. The target repository was empty; the only other repo
  visible (`dianoic`) was inspected read-only as a reference and not modified. No Tableau
  installation, server, workbooks or reference exports were accessible.
- **Decision:** build fresh (D1 in `11-decisions-and-gaps.md`).
- **Incomplete:** an audit of the real Tableau estate (workbook inventory, usage,
  data sources) — requires access.

### Phase 2 — Architecture and data foundation
- **Implemented:** monorepo layout (`02-architecture.md`); connector contract with DuckDB,
  PostgreSQL, SQL Server, Snowflake and Databricks adapters (pooling, read-only sessions,
  timeouts, cancellation, schema discovery, sanitised errors); encrypted credentials;
  semantic layer with versioned datasets, certified/owned fields, joins, hierarchies,
  per-dataset RLS, lineage and validation.
- **Modules:** `intelliq/connectors/*`, `intelliq/semantic/*`, `intelliq/platform/secrets.py`,
  `intelliq/platform/store.py`.
- **Tests:** contract suite (DuckDB + PostgreSQL pass; others skipped), dialect tests,
  semantic resolver tests inside typecheck/query suites.
- **Incomplete:** three adapters unverified against live services; metadata store is SQLite.

### Phase 3 — Dashboard specification
- **Implemented:** IDS v1 as Pydantic models with exported JSON Schema
  (`docs/generated/dashboard.v1.schema.json`), reference validation, version migrations,
  runtime-state compiler that turns viewer state into a query server-side
  (`03-dashboard-spec.md`).
- **Modules:** `intelliq/spec/*`. **Tests:** `test_spec.py` (12), API tests.

### Phase 4 — Query and calculation engine
- **Implemented:** Tableau-compatible formula parser → typed AST → level/type checker →
  dialect SQL via sqlglot; LOD FIXED/INCLUDE/EXCLUDE; filter order of operations;
  Top N; relative dates; table calculations on the result set; `TOTAL()`; bound
  parameters only (no string-built SQL); result cache; per-query timeout and cancel
  (`04-semantic-and-query.md`, `05-calculation-compatibility.md`).
- **Modules:** `intelliq/calc/*`, `intelliq/query/*`.
- **Tests:** function compatibility (123, each checked against an independently written
  oracle query), query engine (30), table calc (10), typecheck (42), parser (20),
  dialects (28), PostgreSQL equivalence (13).
- **Incomplete:** functions listed in gap 8; table-calc addressing beyond table/explicit
  partitions is approximated; several behaviours are marked *validation pending* until
  compared with Tableau output.

### Phase 5 — Visualization engine and UI
- **Implemented:** design tokens (light/dark), component library, app shell, plugin-based
  chart registry (bar, line, area, scatter, pie/donut, heatmap, KPI, table), validated palette
  with colour-follows-entity, escaped tooltips, table view of every chart, dashboard
  runtime (filters, parameters, cross-filter/highlight actions, drill, responsive
  layout), builder (grid layout, shelves, calculated fields, filters,
  parameters, actions, undo/redo, versions, publish), data sources, metrics catalogue,
  admin (`09-design-system.md`, screenshots in `docs/screenshots/`).
- **Modules:** `apps/web/src/*`.
- **Tests:** Vitest (17), Playwright (5), manual screenshot review in light, dark and
  390 px mobile.
- **Incomplete:** per-user saved views, formal WCAG 2.2 audit, navigation actions in the
  UI, drag between shelves, number-format panel (gaps 6, 10, 11, 15).

### Phase 6 — Tableau import
- **Implemented:** safe `.twb`/`.twbx` reading (defusedxml, zip limits, no embedded code
  executed), workbook model, conversion with a four-level compatibility report
  (supported / adjusted / manual / unsupported — nothing dropped silently), field and
  connection mapping, preview on real data, provenance, publish as draft
  (`06-tableau-migration.md`).
- **Modules:** `intelliq/importer/*`, `intelliq/api/routes/imports.py`, import pages.
- **Tests:** importer (17), e2e import flow.
- **Incomplete:** tested only on a hand-authored workbook in `tests/fixtures`;
  needs a corpus of real workbooks.

### Phase 7 — Validation, security, performance
- **Implemented:** reference comparison with tolerances and a validation-case runner
  (`07-validation.md`); RBAC with server-side checks, RLS in every query scope, CSRF,
  argon2, revocable sessions, encrypted secrets, audit log, rate limits, CSV export with
  formula-injection guard (`08-security.md`); measurements and one measured optimisation
  (`10-operations.md`); CI workflow (`.github/workflows/ci.yml`).
- **Incomplete:** **no Tableau parity has been demonstrated** — the framework exists but
  there are no reference exports to compare against. No SSO/MFA. No scheduling,
  subscriptions or PDF/image export.

## What is needed to continue
1. **Real workbooks and reference outputs.** A representative set of `.twb/.twbx` files and,
   for each worksheet, Tableau crosstab exports under agreed filter states. This is the
   only way to move gap 1 from "framework" to "evidence".
2. **Warehouse credentials** (read-only, test schema) for SQL Server, Snowflake and
   Databricks to run the contract suite.
3. **Identity provider** choice (SAML / OIDC provider) for SSO.
4. Decision on the production metadata database (PostgreSQL recommended).

## Recommended next steps
1. Run the importer and validation runner on the first real workbook set; fix gaps in
   priority order of usage.
2. Contract-test the three unverified connectors.
3. PostgreSQL `Store` implementation; OIDC login.
4. Scheduling/subscriptions and PDF/PNG export.
5. WCAG 2.2 audit; navigation actions; builder ergonomics.
