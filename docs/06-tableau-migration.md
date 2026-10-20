# Tableau migration

The importer (`apps/api/intelliq/importer`) is separate from the runtime: it only
produces IDS dashboards and semantic fields. Imported dashboards then run on the
same engine as native ones.

## Workflow (UI: **Tableau import**)
1. **Upload** `.twb` or `.twbx`.
2. **Validate & inspect safely** (`archive.py`): size limit, entry-count limit,
   total/per-entry expansion and compression-ratio limits (zip bombs), rejection of
   absolute paths, `..` traversal, symlinks, multiple root workbooks; nothing is
   extracted to disk; only the root `.twb` is read into memory.
3. **Parse** (`twb_parser.py`) with `defusedxml` (DTDs, entities and external
   references forbidden) into a neutral JSON model: data sources (connection class,
   server/db/schema, relations, joins, custom SQL, metadata records, columns,
   calculations), parameters, worksheets (shelves, marks, encodings, filters, sorts,
   column instances, quick table calcs), dashboards (zones, size), actions.
4. **Map** data sources to Intelliq datasets — auto-suggested by field overlap; field
   mapping auto-matched by caption/remote name and editable per field.
5. **Translate calculations**: parse → rewrite field/parameter references on the tree →
   type-check against the target dataset → canonical formula. Calculations already
   defined identically in the dataset are **reused** (one canonical metric);
   name clashes with different definitions are imported as `Name (Tableau)`;
   parameter-dependent calculations stay worksheet-level so datasets remain
   parameter-free.
6. **Reconstruct** worksheets (mark + shelf inference → chart type and channels),
   filters (categorical, quantitative, relative date, context), sorts, dashboard
   zones (100,000-unit coordinates → 12-column grid, with overlap resolution and
   vertical compaction), quick filters (promoted to interactive dashboard filters,
   seeded with their current selection), parameters, filter/highlight actions.
7. **Report**: every element gets one of **supported / adjusted / manual /
   unsupported** with a message. Nothing is silently dropped.
8. **Preview** converted dashboards live (pending calculated fields are overlaid in
   memory for the preview only).
9. **Apply**: adds calculated fields as a new dataset version (requires
   `manage_datasets`) and creates **draft** dashboards with provenance
   (workbook name, SHA-256, import id, per-component Tableau references).
10. **Validate & publish** through the normal versioning workflow, after comparing
    against reference outputs (`07-validation.md`).

## Coverage
| Workbook element | Status |
|---|---|
| Bar, line, area, circle/shape (scatter), pie, square (heatmap), text table, Automatic marks | Converted |
| Map, polygon, Gantt marks | Unsupported → table fallback, reported |
| Dimensions, measures (Sum/Avg/Min/Max/Count/CountD/Median/Attr/User), date parts and truncations | Converted |
| Quick table calcs: % of total, running total, difference, % difference, rank | Converted (addressing beyond Table/Rows/Columns → adjusted) |
| Calculated fields incl. IF/CASE/IIF, LODs, table calcs | Translated + validated; unsupported functions reported |
| Parameters (all/list/range) | Converted |
| Categorical (include/exclude), quantitative, relative-date, context filters | Converted; Top N filters → manual |
| Computed and alphabetic sorts | Converted; manual sorts → unsupported |
| Dashboard zones: worksheets, quick filters, parameter controls, text | Converted; floating zones → tiled (adjusted); legends rendered inside charts (adjusted) |
| Web page, image, extension objects | Unsupported (reported) |
| Filter and highlight actions within a dashboard | Converted (hover/menu activation → select, adjusted) |
| URL actions | Unsupported by policy (untrusted external links) |
| Cross-dashboard filter actions, navigation actions | Manual |
| Data blending, Measure Names/Values, groups, bins, sets | Unsupported/manual (reported) |
| Custom SQL relations | Manual (must be recreated as an administrator-defined dataset) |
| Packaged extracts (`.hyper`, `.tde`) | Inventoried, not loaded; dashboards query the mapped live dataset |
| Stories | Unsupported (reported) |

## Honesty about fidelity
The **fidelity percentage** is a structural estimate (supported + ½ adjusted over all
items). It says nothing about numeric equality. Numeric parity must be demonstrated
with the validation framework against reference outputs from Tableau.

The automated tests use a **hand-authored** `.twb` modelled on Tableau 2023.x XML
(`apps/api/tests/fixtures/superstore_overview.twb`). Real workbooks saved by Tableau
Desktop/Server have not yet been available and must be used to harden the parser.
