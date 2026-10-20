# Calculation engine and Tableau compatibility

## Pipeline
`parser.py` (lexer + recursive descent) → `ast.py` (immutable typed tree) →
`typecheck.py` (data types + aggregation levels `const/row/agg/table`) →
`compile_sql.py` (sqlglot expressions per dialect) / `table_calc.py` (result stage).

Formulas are never manipulated as strings: imported Tableau calculations are parsed,
their references rewritten on the tree, type-checked against the target dataset,
and rendered back to canonical text for storage.

## Support matrix
The authoritative, generated matrix is
[`generated/calculation-support-matrix.md`](generated/calculation-support-matrix.md):
**78 functions, each with executable compatibility cases** run in CI
(`tests/integration/test_function_compatibility.py`). A function without a case fails
the build.

Supported language features: arithmetic, comparison, `AND/OR/NOT`, `IN`/`NOT IN`,
`IF/ELSEIF/ELSE`, `CASE`, `IIF` (with the 4th "unknown" argument), date literals
`#…#`, string/number/boolean literals, comments, parameters
`[Parameters].[Name]`, datasource-qualified fields, `{FIXED|INCLUDE|EXCLUDE …: …}`
and table-scoped `{…}` LODs, quick table calculations.

## Semantics enforced (where SQL engines differ from Tableau)
| Behaviour | Tableau | Intelliq |
|---|---|---|
| `a / b` | float division; NULL on divide by zero | `CAST(a AS DOUBLE) / NULLIF(b, 0)` on every dialect |
| `INT(x)` | truncates toward zero | explicit CEIL/FLOOR by sign (a plain CAST rounds on PostgreSQL) |
| `MIN(a,b)`/`MAX(a,b)` | NULL if either NULL | CASE expression (LEAST/GREATEST ignore NULLs on some engines) |
| `SQRT`, `LN`, `LOG` outside domain | NULL | NULL (engines may raise) |
| Week start / `DATEPART('weekday')` | locale; US = Sunday, 1 = Sunday | dataset setting, default Sunday; computed arithmetically, not via engine settings (`DATEFIRST`, `WEEK_START`) |
| `DATEPART('week')` | week 1 contains Jan 1 | same, computed portably |
| `DATEDIFF` | boundary crossings | native on DuckDB/SQL Server/Snowflake/Databricks; PostgreSQL computed from calendar fields (verified equal) |
| `DATEADD` on a date | returns a date | cast back to DATE/TIMESTAMP per input |
| String `+` | concatenation, NULL-propagating | `||` / `+` per dialect |
| `NOT IN` on a dimension filter | keeps NULL rows unless NULL excluded | same |
| Contains / starts-with filters | case-insensitive | `LOWER()` on both sides |

## Known semantic differences and validation-pending behaviour
These are deliberate choices or open questions to confirm against real Tableau
output before claiming parity for a workbook that depends on them:

1. **`RUNNING_*` over a NULL mark** carries the running value forward. (Validation pending.)
2. **`TOTAL()`** is exact for every aggregate: SUM/COUNT/MIN/MAX re-aggregate the view
   result; AVG, COUNTD, MEDIAN… are rewritten to `MIN({EXCLUDE <addressed dims> : AGG(x)})`
   and computed over the underlying rows (oracle-tested). Whether Tableau's TOTAL sees
   measure (HAVING) filters is not documented — validation pending.
3. **`ATTR()`** returns NULL for mixed values; the renderer would need to show `*`
   like Tableau (it currently shows "–").
4. **`RANK_PERCENTILE` with ties** uses (count below)/(n−1). (Validation pending.)
5. **Table-calculation addressing**: quick table calcs support "Table" and explicit
   `partition_by`; Tableau's "Pane", "Cell" and "Specific dimensions with
   at-the-level/restarting-every" are approximated (reported as *adjusted* on import).
6. **`MEDIAN`** is unsupported on SQL Server.
7. **Data blending, sets, groups, bins, `SCRIPT_*`, regex functions, spatial functions,
   `WINDOW_*` with start/end offsets** are not supported; the importer reports them.
8. **`TODAY()`/`NOW()`** use the database server's time zone.
9. Boolean-valued SELECT expressions on SQL Server (no boolean type) are not yet
   wrapped in CASE.
