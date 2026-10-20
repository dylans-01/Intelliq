# Phase 1 — Environment and application audit

_Audit date: 2026-10-20. Everything below was observed directly in the session
environment unless marked as an assumption._

## 1. What was accessible

| Item | Finding |
|---|---|
| `dylans-01/Intelliq` repository (target) | **Empty.** No commits on any branch; the remote had no refs. There was no existing Intelliq application to audit or migrate in place. |
| Other repositories visible to the account | One: `dylans-01/dianoic` (private). Attached read-only and inspected with file-reading tools; **not modified**. |
| Tableau Desktop / Server / Cloud | **Not accessible.** No Tableau installation, server URL, credentials, published workbooks or exported metadata were available. |
| Running applications / browser previews | None pre-existing. The new application was run locally and inspected with a headless browser (Chromium via Playwright). |
| Desktop control | Not available in this cloud container. |
| Toolchain | Python 3.13, Node 22, pnpm, uv, Chromium 1194 (Playwright). Docker CLI present but no daemon. Outbound package installs (PyPI, npm) work. |
| Databases | None provided. DuckDB (embedded) used for development; an embedded PostgreSQL 16.2 (`pgserver`, run from a Python 3.12 venv) used to run the connector contract suite. No SQL Server, Snowflake or Databricks instance was available. |

**Consequence:** the "existing application" referred to in the brief could not be
found in the target repository. The only candidate, Dianoic, is assessed below as
a *reference* — it is a different product (small-business CSV analytics with AI
insights) and was not treated as Intelliq's codebase.

## 2. Assessment of `dylans-01/dianoic` (reference only)

### Architecture and stack
- Next.js 16 (App Router) + React 19 + TypeScript + Tailwind 4; Supabase
  (Postgres, Auth, Storage, RLS); Anthropic SDK for "Dino" AI insights; Stripe;
  Resend; visx/d3/react-grid-layout for charts and layout.
- ~180 source files in `src/`; 7 Supabase SQL migrations; Vitest unit tests for
  aggregation helpers.
- Data path: CSV uploads → parsed in memory → aggregated in TypeScript
  (`src/lib/dashboard/aggregators.ts`, 731 lines) → widgets. There is **no SQL
  connector layer, no semantic layer, no query planner, no calculation language.**

### UI / UX problems observed in the code
- **Hard-coded colours** despite the project's own "never hardcode colours"
  rule: 367 hex/rgba literals across 32 component files.
- **Duplicated primitives:** three `CommandPalette` components, two `BarChart`,
  two `LineChart`, two `DonutChart`, two `Widget` implementations.
- **Very large page components:** `src/app/page.tsx` 1,683 lines;
  `SettingsPanel.tsx` 703 lines; `weekly-report/page.tsx` 717 lines — layout,
  data shaping and styling interleaved.
- Charts receive pre-shaped arrays; no shared spec, so each widget re-implements
  formatting, legends and tooltips.
- Known placeholders documented in its own `CLAUDE.md` ("StatCard values are
  hardcoded").

### Worth retaining (as ideas, not code)
- Theme-before-first-paint pattern (`ThemeInit`) — reused as `public/theme-init.js`.
- Row-level security mindset (every query org-scoped) — generalised as
  per-dataset RLS rules applied in every query scope.
- Vitest-tested pure aggregation functions — the same discipline is applied to
  Intelliq's runtime and spec operations.

### Technical debt and coupling
- Business rules (restaurant/retail metrics), presentation and data shaping are
  coupled inside widgets; the AI prompt layer depends on in-memory row arrays.
- No versioned dashboard representation: layouts are stored as widget lists tied
  to component names.

**Decision:** build Intelliq fresh in the target repository with a modular
architecture; reuse nothing from Dianoic's code. Rationale: it targets a
different domain, has no SQL/semantic/query layers to build on, and its UI
problems are structural, not cosmetic.

## 3. Data connections and SQL dependencies
None were available. Intelliq therefore ships:
- a deterministic **synthetic** sample warehouse shaped like Tableau's
  "Sample - Superstore" (orders/people/returns), clearly labelled as synthetic;
- adapters for SQL Server, Snowflake, Databricks SQL and PostgreSQL behind one
  contract, plus DuckDB for development.

## 4. Requirements matrix

| # | Requirement (brief §) | Status | Evidence |
|---|---|---|---|
| R1 | Pluggable connectors: SQL Server, Snowflake, Databricks, PostgreSQL, DuckDB (2A) | DuckDB + PostgreSQL verified; others implemented, SQL generation tested only | `connectors/`, `test_connector_contract.py`, `test_dialects.py` |
| R2 | Secure credentials, never in specs/SQL/logs/frontend (2A, 7) | Done | Fernet at rest, write-only API, `test_connection_secrets_are_write_only` |
| R3 | Semantic layer: fields, metrics, joins, hierarchies, versioning, lineage (2B) | Done (ownership is a free-text field) | `semantic/`, Metrics page |
| R4 | Query planner: filters, aggregation, joins, dates, distinct, nulls, params, calcs, windows/table calcs, caching, dialects, limits, cancellation, timeouts (2C) | Done | `query/planner.py`, oracle tests |
| R5 | No SQL from untrusted strings (2C, 7) | Done | bound params, allowlisted identifiers, `test_filters_bind_values_never_inline` |
| R6 | Spec-driven renderer, plugin chart types (2D) | Done (8 types) | `viz/plugins/registry.ts` |
| R7 | Dashboard composition: layout, filters, cross-filter, drill, tooltips, parameters, actions (2E) | Done; navigation actions modelled but not yet wired in UI | `dashboard/`, e2e tests |
| R8 | Platform services independent of rendering/query (2F) | Done (scheduling/background jobs: not started) | `platform/` |
| R9 | `.twb`/`.twbx` import with mapping, translation, report, preview, publish, provenance (3) | Done for the documented subset | `importer/`, Import pages |
| R10 | Versioned internal spec with migrations, JSON Schema (4) | Done | `spec/`, `docs/generated/dashboard.v1.schema.json` |
| R11 | Rebuilt UI: design system, shell, dashboard, builder, light/dark, accessible (5) | Done; formal accessibility audit not yet performed | `apps/web`, screenshots in PR |
| R12 | Typed calculation engine with compatibility tests and support matrix (6) | Done | `calc/`, `docs/generated/calculation-support-matrix.md` |
| R13 | Security: RBAC, server-side authz, audit, limits, safe parsing (7) | Done | `docs/08-security.md` |
| R14 | Validation framework with tolerances (8) | Framework done; **no Tableau reference outputs yet** | `validation/`, `test_validation_runner.py` |
| R15 | Performance measurement (9) | Baseline measured | `docs/10-operations.md` |

## 5. Unknowns and assumptions
1. **Which Tableau dashboards are in scope** — unknown. Parity work requires the
   actual workbooks (`.twb`/`.twbx`) and, ideally, access to the live views or
   crosstab exports for reference outputs.
2. **Production databases** — unknown which of SQL Server/Snowflake/Databricks
   hold the data; connection details and a test schema are needed to verify
   those adapters.
3. **Identity provider** — assumed local accounts for now; SSO (OIDC/SAML) is
   designed for but not implemented.
4. **Locale/time zone** — Tableau US defaults assumed (Sunday week start,
   server-time `TODAY()`); configurable per dataset where implemented.
5. **Deployment target** — assumed a container behind a TLS-terminating proxy.

## 6. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Real workbooks use features outside the supported subset (maps, sets, groups, bins, blends, Measure Names) | High | High | Importer never drops silently; report drives a prioritised backlog (Phase 7) |
| LOD / table-calc semantics differ from Tableau on edge cases | Medium | High | Oracle tests + validation framework against Tableau exports before sign-off |
| Untested adapters (SQL Server/Snowflake/Databricks) fail on real services | Medium | High | Contract suite runs against any engine via env config; run before production use |
| Single-node SQLite metadata store limits scale-out | Medium | Medium | Store interface is narrow; PostgreSQL implementation planned |
| Large extracts in `.twbx` are the only copy of some data | Medium | High | Report flags extracts; requires a live dataset mapping |

## 7. Recommended sequence (as executed)
Phase 1 audit → Phase 2 architecture → Phase 3 foundation (design system, auth,
schema validation, DuckDB connector) → Phase 4 vertical slice (SQL → result →
chart → saved dashboard) → Phase 5 builder → Phase 6 Tableau import →
Phase 7/8 partially (PostgreSQL verification, performance baseline, security
tests). See `00-status.md` for what remains.
