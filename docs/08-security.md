# Security model

Intelliq is treated as an enterprise data application: every request is
authenticated, authorised on the server, scoped to a workspace, and audited.
The web client only *reflects* permissions; it never enforces them.

## Identity and sessions
- Local accounts with **argon2id** password hashes (min. 10 characters); constant-time
  verification with a dummy hash so login timing does not reveal whether an e-mail exists;
  identical error for unknown user and wrong password.
- **Server-side sessions**: 256-bit random token in an `HttpOnly`, `SameSite=Lax` cookie
  (`Secure` via `INTELLIQ_COOKIE_SECURE=1`); only its SHA-256 is stored; expiry
  (default 12 h); all sessions revoked on password change or deactivation.
- **CSRF**: per-session token required in `X-Intelliq-CSRF` for every state-changing request.
- **Rate limits**: login (per IP and per IP+e-mail) and queries (per user).
- SSO (OIDC/SAML) is a planned provider behind the same session API.

## Authorization (RBAC per workspace)
| Capability | viewer | author | admin |
|---|:-:|:-:|:-:|
| View published dashboards, use filters/parameters/actions/drill | ✓ | ✓ | ✓ |
| View drafts and versions; edit, duplicate, delete, publish dashboards | | ✓ | ✓ |
| Ad-hoc queries, formula validation, view generated SQL, browse datasets | | ✓ | ✓ |
| Export data (CSV) | workspace setting | ✓ | ✓ |
| Import Tableau workbooks | | ✓ | ✓ |
| Manage datasets (semantic layer), connections, users; read audit log | | | ✓ |

Every route declares its capability (`require(Cap.…)` in `api/deps.py`). Resources are
looked up **within the caller's workspace**; foreign ids return 404.

## Data-access boundary
- **No arbitrary SQL.** Users never submit SQL. Identifiers come only from the
  administrator-authored semantic model (validated against the discovered schema);
  every user value is a **bound parameter** behind an unguessable per-query token.
  Formula literals are rendered by sqlglot's dialect-aware escaping (tested with
  quotes, backslashes and `%`).
- **Viewers cannot shape queries.** They send only runtime state validated against the
  stored, published spec: values for declared filters, parameter values within the
  declared domain, selections for declared actions, a drill path.
- **Row-level security** is applied in every query scope — main query, LOD subqueries,
  Top N subqueries — and is part of the cache key via the rendered SQL.
- **Filter value enumeration** for viewers is limited to fields exposed as dashboard
  filters (`/dashboards/{id}/filters/{fid}/values`), with RLS applied.
- **Resource limits**: per-dataset row cap and timeout, server-side cancellation,
  bounded result cache, request body limits, formula length/depth limits.
- **Connections** run read-only where the driver allows (`READ ONLY` transactions on
  PostgreSQL, `ApplicationIntent=ReadOnly` on SQL Server, DuckDB `read_only` with
  external file access disabled and a path allow-list).
- **Error sanitisation**: driver errors are trimmed; viewers get a generic message;
  details are logged server-side.

## Secrets
- Connection credentials are write-only through the API and encrypted at rest with
  Fernet (AES-128-CBC + HMAC-SHA256). The key comes from `INTELLIQ_SECRET_KEY` (inject from
  a KMS/secret manager); the server refuses to start without it outside dev mode.
- Credentials are never placed in dashboard specs, generated SQL, API responses,
  audit entries or logs (tested).

## Untrusted input
- Workbook files: see `06-tableau-migration.md` (defusedxml, zip limits, no extraction,
  no execution, URL actions not imported).
- CSV exports neutralise spreadsheet formula injection (`=`, `+`, `-`, `@`, tab, CR).
- Chart tooltips escape all labels before ECharts renders HTML.
- HTTP security headers: strict CSP (`script-src 'self'`, `frame-ancestors 'none'`),
  `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`,
  `Cache-Control: no-store` on API responses.

## Audit log
Logins (success/failure), logouts, password changes, user/role/attribute changes,
connection create/update (never secrets), dataset versions, dashboard
create/save/publish/unpublish/duplicate/delete, imports (analysed/rejected/applied),
exports, and every query (dataset, rows, duration, cache hit — not values).

## Tests that enforce this
`tests/integration/test_api.py` (auth, CSRF, rate limit, headers, RBAC matrix, draft
visibility, viewer injection attempts, RLS via API, filter-value scoping, export
permission + formula injection, secrets write-only/encrypted, DuckDB path allow-list,
hostile workbook uploads, last-admin guard), `test_query_engine.py` (bound values, RLS
in LODs, external access disabled), `test_importer.py` (zip slip, bombs, symlinks, XXE).

## Known gaps
No SSO/MFA yet; no per-dashboard ACLs beyond workspace roles; audit log is in the
metadata DB (export to a SIEM is not built); secret rotation requires re-entering
credentials; no column-level security.
