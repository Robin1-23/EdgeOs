// Pick MF1 and MF2 metrics out of an envelope by id for tables. Selection only: no value is derived here.
import type { Envelope, Metric } from "./types.ts";

const byId = (e: Envelope) => new Map(e.metrics.map((m) => [m.id, m]));

export type ReturnsRow = { years: number; fund?: Metric; category?: Metric; difference?: Metric };

/** MF2's n-year return, its category average and the difference, for the windows MF2 computed. */
export function returnsRows(mf2: Envelope): ReturnsRow[] {
  const m = byId(mf2);
  return [1, 3, 5]
    .map((n) => ({ years: n, fund: m.get(`return_${n}y`), category: m.get(`category_return_${n}y`), difference: m.get(`vs_category_${n}y`) }))
    .filter((r) => r.fund || r.category);
}

export type OverlapCell = { overlap?: Metric; common?: Metric };

/** MF1's pairwise overlap for every pair of `codes`, whichever order MF1 named the pair in. */
export function overlapCell(mf1: Envelope, a: string, b: string): OverlapCell {
  const m = byId(mf1);
  const key = m.has(`overlap_${a}_${b}`) ? `${a}_${b}` : `${b}_${a}`;
  return { overlap: m.get(`overlap_${key}`), common: m.get(`common_stocks_${key}`) };
}
