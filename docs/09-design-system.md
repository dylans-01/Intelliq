# Design system

## Direction
Premium, minimal, data-dense: warm-neutral surfaces, hairline borders instead of heavy
cards, one accent, restrained motion, and the data as the only loud element. No
generic admin template, no decorative gradients, no oversized headings.

## Tokens (`apps/web/src/design-system/tokens.css`)
- **Typography:** Inter (variable, self-hosted — no third-party font requests);
  11–16 px UI scale, 30 px KPI values; proportional figures for hero numbers,
  `tabular-nums` for table columns and axes.
- **Spacing:** 4 px base (2…48). **Radii:** 4/6/8/12. **Controls:** 32 px (28 px compact).
- **Surfaces:** page, surface-1/2/3, sunken, overlay. **Ink:** 1/2/3/muted/inverse.
- **Status:** good/warning/serious/critical — always paired with an icon and a label.
- **Elevation:** three shadows. **Motion:** 120/180 ms, respects `prefers-reduced-motion`.
- **Themes:** light and dark are each *selected* (not inverted). The theme follows the OS
  unless the user picks one; applied before first paint to avoid flashes.

## Data-visualisation palette
Categorical order (light / dark) — validated with the data-viz palette validator:

| Slot | Light | Dark |
|---|---|---|
| 1 | `#2a78d6` | `#3987e5` |
| 2 | `#eb6834` | `#d95926` |
| 3 | `#1baf7a` | `#199e70` |
| 4 | `#eda100` | `#c98500` |
| 5 | `#e87ba4` | `#d55181` |
| 6 | `#008300` | `#008300` |
| 7 | `#6250d6` | `#9085e9` |
| 8 | `#e34948` | `#e66767` |

Validator results: light (surface `#fcfcfb`) — lightness band, chroma floor, CVD
separation (worst adjacent ΔE 9.1) and normal-vision floor (19.6) **pass**; three light
slots are below 3:1 contrast, so every chart ships a **table view** (relief rule).
Dark (surface `#1a1a19`) — all checks pass (worst adjacent CVD ΔE 8.4, contrast ≥ 3:1).

Rules implemented in the renderer:
- Hues are assigned in fixed order and **follow the entity**: a dashboard-wide registry keeps
  "Region = West" the same colour in every chart and across filter changes.
- Members past the eighth share a neutral "other" colour; scatter colour groups are capped
  at three (all-pairs validation) with the rest folded into "Other".
- **No dual axes**: measures with different formats render as stacked small multiples.
- Marks: bars ≤ 24 px with 4 px rounded data-ends, 2 px lines, ≥ 8 px markers with a 2 px
  surface ring, 2 px surface gaps in stacks and donuts, solid hairline gridlines.
- Legends for ≥ 2 series (mirroring the mark shape); ≤ 4 line series are also end-labelled;
  single series have no legend box.
- Tooltips: value first, label second, line/rect keys; all text HTML-escaped.
- Refetches keep the previous frame at reduced opacity (no skeleton flash).

## Components (`design-system/components`)
Button / IconButton, Input / SearchInput / Select / Textarea / Field / Checkbox / Switch,
Badge, Spinner, Skeleton, EmptyState / ErrorState / ForbiddenState / PageLoader, Tabs,
Segmented, Popover / Menu / Dialog (focus-trapped, Escape to close), Toast, Combobox
(searchable single/multi select, lazy options), DataTable (sort, search, paginate,
selectable rows; used for lists and every chart's table twin).

## Accessibility
Keyboard reachable controls with visible focus rings; skip-to-content link; ARIA roles
for tabs, menus, dialogs, listboxes and switches; charts expose an accessible name and a
table view; colour is never the only carrier of meaning (legends, labels, status icons).
A formal WCAG 2.2 audit (screen-reader pass, contrast of all text pairs, zoom/reflow) has
**not** yet been carried out.
