import test from "node:test";
import assert from "node:assert/strict";
import { overlapCell, returnsRows } from "./funds.ts";
import type { Envelope, Metric } from "./types.ts";

const metric = (id: string, value: number): Metric => ({ id, name: id, value, unit: "ratio", formula_id: "f", source_query_id: "s", formula: null });
const env = (agent: string, ms: [string, number][]): Envelope => ({
  agent_id: agent, subject: "x", as_of: "2026-10-27", metrics: ms.map(([i, v]) => metric(i, v)),
  findings: [], flags: [], sources: [], confidence: { level: "high", reason: "" }, gaps: [],
});

test("returnsRows keeps only windows MF2 computed", () => {
  const rows = returnsRows(env("MF2", [["return_1y", 0.12], ["category_return_1y", 0.1], ["vs_category_1y", 0.02], ["return_3y", 0.15]]));
  assert.deepEqual(rows.map((r) => [r.years, r.fund?.value, r.category?.value, r.difference?.value]), [
    [1, 0.12, 0.1, 0.02],
    [3, 0.15, undefined, undefined],
  ]);
});

test("overlapCell finds the pair in either order", () => {
  const e = env("MF1", [["overlap_100001_200001", 0.25], ["common_stocks_100001_200001", 1]]);
  assert.equal(overlapCell(e, "200001", "100001").overlap?.value, 0.25);
  assert.equal(overlapCell(e, "100001", "200001").common?.value, 1);
  assert.deepEqual(overlapCell(e, "100001", "300001"), { overlap: undefined, common: undefined });
});
