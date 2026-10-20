# Validation and one-for-one parity

Looking similar is not parity. A dashboard is considered at parity only when the
checks below pass against references produced by Tableau for the same data.

## Procedure per dashboard
1. Capture the Tableau dashboard: screenshots at a fixed size, worksheet list,
   filter options and defaults, interactions (actions, drill, sorts), calculation
   definitions (from the workbook).
2. Export reference data for each worksheet and each interesting state: Tableau
   *Worksheet → Export → Crosstab* or *View Data → Summary* as CSV, with the filter
   state recorded.
3. Import the workbook; resolve every *manual* item; record every *adjusted* item.
4. Write a validation case (below) and run `intelliq validate case.json --report out.json`.
5. Compare filters, drill-downs, sorting and interactions (Playwright scripts in
   `tests/e2e` show the pattern).
6. Compare layout/typography/chart configuration against screenshots.
7. Log every discrepancy; fix or document it; repeat until acceptance criteria pass.

## Validation case format
```json
{
  "name": "Executive Overview vs Tableau 2023.3",
  "connection": {"kind": "duckdb", "config": {"path": "warehouse.duckdb"}},
  "dataset": "dataset.json",
  "dashboard": "dashboard.json",
  "tolerance": {"absolute": 0.005, "relative": 1e-9},
  "checks": [
    {"worksheet": "by_region", "reference": "by_region.csv",
     "state": {"filters": {"f_segment": {"values": ["Corporate"]}}},
     "keys": {"Region": "d0"}, "values": {"Sales": "m0", "Profit Ratio": "m1"}}
  ]
}
```
`keys`/`values` map reference CSV headers to result column ids (`d0…` dimensions,
`m0…` measures in field order).

## Comparison rules (`intelliq/validation/compare.py`)
| Topic | Rule |
|---|---|
| Numeric tolerance | `|actual − expected| ≤ absolute + relative × |expected|`; default absolute 0.005 (half a cent), relative 1e‑9 |
| Rounding | References are compared at the precision exported; set `absolute` to half a unit of the exported precision |
| Number formats | `$`, thousands separators and `%` (→ fraction) are parsed |
| NULLs | NULL only equals NULL; empty, `null`, `%null%` cells are NULL |
| Dates | Keys normalised to ISO; `MM/DD/YYYY` and `YYYY-MM-DD HH:MM:SS` exports accepted |
| Time zones | Dates are compared as calendar values; `NOW()`/`TODAY()` depend on server time zone — fix the anchor (relative date `anchor`) when validating |
| Week boundaries | Must use the same week start as the Tableau locale (dataset setting) |
| Rows | Missing and unexpected rows are both discrepancies |

## What has been validated so far
| Check | Status |
|---|---|
| Engine results vs independently written SQL on the synthetic warehouse (aggregates, joins, LODs, Top N, table calcs, RLS, parameters, relative dates) | **Passing** (`test_query_engine.py`) |
| Same answers on DuckDB and PostgreSQL 16 for 10 query families | **Passing** (`test_connector_contract.py`) |
| Validation runner detects value differences and extra/missing rows | **Passing** (`test_validation_runner.py`) |
| Parity with Tableau outputs for any real workbook | **Not yet possible** — no Tableau access or reference exports were available |

Screenshot comparison: Playwright can capture dashboards deterministically (bundled
Inter font, fixed viewport); when Tableau reference screenshots are supplied, add
`expect(page).toHaveScreenshot()` assertions with an agreed pixel tolerance.
