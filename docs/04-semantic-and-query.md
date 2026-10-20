# Semantic layer and query engine

## Semantic model (`intelliq/semantic`)
A **dataset** binds a connection to a base table plus explicit joins and declares
business fields:

- **Column fields** map to `alias.column`; **calculated fields** use the formula language.
- **Metrics** are aggregate-level calculated fields (e.g. `Profit Ratio =
  SUM([Profit]) / SUM([Sales])`). Each has one definition, referenced by id from any
  dashboard; `certified` marks canonical ones.
- **Hierarchies** (coarse → fine field lists) enable drill-down.
- **Row-level security** rules: rows where `field` ∈ the user's attribute values;
  users without the attribute see nothing; exempt roles configurable.
- **Settings:** week start, cache TTL, row cap, query timeout.
- **Versioning:** every change is a new dataset version with a note; dashboards
  always use the current version. Validation (types, aggregation levels, cycles,
  RLS fields, missing columns) runs before a version is saved.
- **Lineage:** for each field — direct dependencies, dependants, and the physical
  columns it ultimately reads (shown on the Metrics page).

Identifiers in SQL come only from this administrator-authored model and are
validated against the connection's discovered schema when saved.

## Planner (`intelliq/query/planner.py`)
1. Resolve each field use to an expression tree; type-check with aggregation levels.
2. Wrap measures in their aggregation and quick table calculation.
3. **Split table calculations**: aggregate sub-expressions become hidden SQL columns;
   the rest is evaluated on the returned rows (`calc/table_calc.py`).
4. **Plan LODs**:
   - `AGG({LOD})` in the view → aggregate LOD values over distinct
     (view dimensions ∪ LOD dimensions) — coarser LODs are replicated once per mark,
     finer LODs aggregate at their own grain (Tableau's documented behaviour).
   - LODs inside row-level expressions → grouped subquery joined at row level.
   - FIXED subqueries see datasource + context filters only; INCLUDE/EXCLUDE also
     see dimension filters. Row-level security applies in **every** scope.
5. **Bind** fields to physical columns; LEFT joins not needed by the query are culled
   (INNER joins are kept because they change row sets).
6. **Filters** by stage; Top N computed after context filters only.
7. **Values are bound parameters** behind unguessable per-query tokens, rendered to
   each driver's positional marker (`?` or `%s`, with `%` escaped for psycopg).
8. Default ordering by dimensions; limit = min(user limit, dataset cap) + 1 to detect
   truncation (table calcs see all marks, user limit applied afterwards).

## Executor (`intelliq/query/executor.py`)
- **Cache** key = rendered SQL + bound values + dataset id/version + connection
  revision. RLS predicates are part of the SQL, so two users share an entry only if
  their entitlements produce identical queries. TTL per dataset; LRU bounded by cells.
- **Cancellation**: every query registers a token; the browser sends a query id and
  cancels superseded requests; connectors interrupt the driver.
- **Timeouts**: enforced in the connector (driver interrupt) and server-side where the
  engine supports it (PostgreSQL `statement_timeout`).

## Connectors (`intelliq/connectors`)
| Connector | Dialect | Parameters | Timeout / cancel | Verification |
|---|---|---|---|---|
| DuckDB | duckdb | `?` | `interrupt()` | Contract suite (CI) — read-only, external access disabled, path allow-list |
| PostgreSQL | postgres | `%s` | `statement_timeout` + protocol cancel; READ ONLY txn | Contract suite against PostgreSQL 16.2 — identical answers to DuckDB |
| SQL Server | tsql | `?` | ODBC query timeout + `cancel()`; `ApplicationIntent=ReadOnly` | SQL generation only |
| Snowflake | snowflake | qmark `?` | statement timeout + `SYSTEM$CANCEL_QUERY` | SQL generation only |
| Databricks SQL | databricks | native `?` | `cursor.cancel()` | SQL generation only |

Run the contract suite against any engine by setting `INTELLIQ_TEST_<KIND>` to a JSON
connection config (see `tests/integration/test_connector_contract.py`).
