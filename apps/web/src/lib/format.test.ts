import test from "node:test";
import assert from "node:assert/strict";
import { anchorId, formatChange, formatDate, formatValue, monthsBefore } from "./format.ts";

test("formatValue by unit", () => {
  assert.equal(formatValue(1001.2, "INR"), "₹1,001.20");
  assert.equal(formatValue(1234567.8, "INR"), "₹12,34,567.80"); // Indian digit grouping
  assert.equal(formatValue(206.04, "INR cr"), "₹206 cr");
  assert.equal(formatValue(45.27, "INR cr"), "₹45.3 cr");
  assert.equal(formatValue(-1.3328, "INR"), "−₹1.33");
  assert.equal(formatValue(-1250.4, "INR cr"), "−₹1,250 cr");
  assert.equal(formatValue(0.237, "ratio"), "23.7%");
  assert.equal(formatValue(-0.068, "ratio"), "−6.8%");
  assert.equal(formatValue(27.274, "x"), "27.3×");
  assert.equal(formatValue(-5.25, "pp"), "−5.3 pp");
  assert.equal(formatValue(2.5, "pp"), "+2.5 pp");
  assert.equal(formatValue(20, "points"), "20");
  assert.equal(formatValue(50.911, null), "50.91");
  assert.equal(formatValue(12, null), "12");
  assert.equal(formatValue("mixed", null), "mixed");
  assert.equal(formatValue(Number.NaN, "INR"), "—");
});

test("formatChange is signed, two decimals", () => {
  assert.equal(formatChange(0.01), "+1.00%");
  assert.equal(formatChange(-0.000151), "−0.02%");
  assert.equal(formatChange(0), "0.00%");
  assert.equal(formatChange(null), "—");
});

test("dates and anchors", () => {
  assert.equal(formatDate("2022-08-30"), "30 Aug 2022");
  assert.equal(formatDate("2022-08-29T18:30:00+00:00"), "30 Aug 2022"); // midnight IST
  assert.equal(formatDate("2022-08-30T15:30:00+05:30"), "30 Aug 2022");
  assert.equal(monthsBefore("2022-08-30", 12), "2021-08-30");
  assert.equal(monthsBefore("2022-08-30", 6), "2022-02-28"); // clamped to the month's end
  assert.equal(monthsBefore("2024-03-31", 1), "2024-02-29");
  assert.equal(monthsBefore("2022-01-15", 60), "2017-01-15");
  assert.equal(anchorId("M5:finding:headline_6"), "M5-finding-headline_6");
});
