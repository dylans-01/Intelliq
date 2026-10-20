# Acceptance criteria and current status

Status reflects what is implemented **and** covered by tests in this repository as of
2026-10-20. "Partial" means usable but with documented gaps.

| # | Criterion (from the brief) | Status | Evidence / remaining work |
|---|---|---|---|
| 1 | Clean, premium, cohesive UI | **Met (first iteration)** | Design system + every screen built on it; light/dark; responsive; screenshots in `docs/screenshots/`. Formal design review and WCAG audit outstanding |
| 2 | Independent runtime without Tableau | **Met** | No Tableau dependency anywhere; imported dashboards run on the same engine |
| 3 | Reusable connectors and centralized semantic layer | **Partial** | DuckDB + PostgreSQL verified; 3 adapters unverified against live services; semantic layer versioned with lineage |
| 4 | Functioning query and calculation engine | **Met for the documented subset** | 78 functions with executable cases; LODs, Top N, filter stages, table calcs; oracle tests; DuckDB/PostgreSQL equivalence |
| 5 | Dashboard spec independent of renderer | **Met** | IDS v1 (Pydantic + JSON Schema + migrations); renderer consumes spec + results only |
| 6 | Editor with persistence and interactions | **Met (first iteration)** | Builder with grid layout, shelves, calcs, filters, parameters, actions, undo/redo, versions, publish; e2e tested. Saved views not built |
| 7 | Tableau importer with transparent compatibility reporting | **Met for supported subset** | Four-level report, nothing dropped silently, preview, provenance; needs hardening on real workbooks |
| 8 | Reproducible numerical and behavioural validation | **Framework met; parity not demonstrated** | Validation runner + oracle + contract suites; **no Tableau reference outputs available yet** |
| 9 | Enterprise access controls and secure data handling | **Partial** | RBAC, server-side checks, RLS in every scope, CSRF, encrypted secrets, audit, safe parsing — no SSO/MFA, column security |
| 10 | Modular architecture with replaceable subsystems | **Met** | Module contracts in `02-architecture.md`; plugin registries for connectors and charts |

## Proposed acceptance tests for a migrated dashboard
A migrated dashboard is accepted when, for every worksheet and for each agreed filter
state: (a) the validation case passes with the agreed tolerance; (b) filters, actions,
drill paths and sorts behave as recorded in Tableau (scripted browser test);
(c) screenshot comparison is within the agreed tolerance or differences are approved by
the owner; (d) every *adjusted/manual/unsupported* report item is resolved or accepted in
writing.
