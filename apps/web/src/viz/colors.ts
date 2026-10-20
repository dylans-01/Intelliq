/**
 * Categorical colour assignment.
 *
 * Colour follows the entity, never its rank: once a member (e.g. Region =
 * "West") receives a slot it keeps it for the life of the dashboard, across
 * every worksheet and every filter change. Slots are handed out in the fixed,
 * validated palette order; members past the eighth slot share the neutral
 * "other" colour instead of generating new hues.
 */

export const SLOT_COUNT = 8;

export class ColorRegistry {
  private slots = new Map<string, Map<string, number>>();

  /** Slot index 0..7, or -1 for "other". */
  slot(scope: string, member: string): number {
    let scoped = this.slots.get(scope);
    if (!scoped) {
      scoped = new Map();
      this.slots.set(scope, scoped);
    }
    const existing = scoped.get(member);
    if (existing !== undefined) return existing;
    const next = scoped.size < SLOT_COUNT ? scoped.size : -1;
    scoped.set(member, next);
    return next;
  }

  /** Pre-register members in a stable order (e.g. sorted domain) before first paint. */
  seed(scope: string, members: string[]) {
    for (const m of members) this.slot(scope, m);
  }
}

export interface ChartPalette {
  series: string[];
  other: string;
  surface: string;
  ink1: string;
  ink2: string;
  muted: string;
  grid: string;
  axis: string;
  seq: [string, string, string, string];
  overlay: string;
  border: string;
}

export function readPalette(): ChartPalette {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string) => css.getPropertyValue(name).trim();
  return {
    series: Array.from({ length: SLOT_COUNT }, (_, i) => v(`--series-${i + 1}`)),
    other: v("--series-other"),
    surface: v("--surface-1"),
    ink1: v("--ink-1"),
    ink2: v("--ink-2"),
    muted: v("--chart-label"),
    grid: v("--chart-grid"),
    axis: v("--chart-axis"),
    seq: [v("--seq-100"), v("--seq-300"), v("--seq-500"), v("--seq-700")],
    overlay: v("--surface-overlay"),
    border: v("--border"),
  };
}

export function colorFor(palette: ChartPalette, slot: number): string {
  return slot >= 0 && slot < palette.series.length ? palette.series[slot] : palette.other;
}
