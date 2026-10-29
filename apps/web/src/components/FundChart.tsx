"use client";

import { ColorType, createChart, LineSeries } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { formatDate, monthsBefore } from "@/lib/format";
import type { FundSeries } from "@/lib/types";

const RANGES = [
  { label: "1Y", months: 12 },
  { label: "3Y", months: 36 },
  { label: "5Y", months: 60 },
  { label: "Max", months: 600 },
];

function css(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Fund NAV against its category benchmark, both rebased to 100 by the API, up to the report date. */
export function FundChart({ code, asOf }: { code: string; asOf: string }) {
  const box = useRef<HTMLDivElement>(null);
  const [months, setMonths] = useState(36);
  const [series, setSeries] = useState<FundSeries | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    setError(null);
    fetch(`/api/funds/${encodeURIComponent(code)}/series?from=${monthsBefore(asOf, months)}&to=${asOf}`, { signal: ctl.signal })
      .then(async (r) => {
        const body = await r.json();
        if (!r.ok) throw new Error(body.detail ?? "NAV history not available");
        setSeries(body as FundSeries);
      })
      .catch((e: Error) => e.name !== "AbortError" && setError(e.message));
    return () => ctl.abort();
  }, [code, asOf, months]);

  useEffect(() => {
    if (!box.current || !series?.fund.length) return;
    const chart = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: css("--muted"), attributionLogo: true },
      grid: { vertLines: { color: css("--line") }, horzLines: { color: css("--line") } },
      rightPriceScale: { borderColor: css("--line") },
      timeScale: { borderColor: css("--line") },
    });
    chart.addSeries(LineSeries, { color: css("--accent"), lineWidth: 2, title: "Fund" })
      .setData(series.fund.map((p) => ({ time: p.date, value: p.value })));
    if (series.benchmark) {
      chart.addSeries(LineSeries, { color: css("--muted"), lineWidth: 1, title: series.benchmark.name })
        .setData(series.benchmark.points.map((p) => ({ time: p.date, value: p.value })));
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [series]);

  return (
    <section aria-labelledby="nav-chart-title" className="rounded-lg border border-line p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 id="nav-chart-title" className="text-sm font-semibold" title={series?.formula}>
          Growth of 100{series?.benchmark ? ` vs ${series.benchmark.label}` : ""}
        </h2>
        <div role="group" aria-label="Chart range" className="flex gap-1">
          {RANGES.map((r) => (
            <button
              key={r.label}
              type="button"
              aria-pressed={months === r.months}
              onClick={() => setMonths(r.months)}
              className={`rounded px-2 py-1 text-xs border ${months === r.months ? "border-accent text-accent" : "border-line text-muted hover:text-foreground"}`}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>
      {error ? (
        <p className="py-10 text-center text-sm text-muted">Chart not available: {error}</p>
      ) : (
        <>
          <div ref={box} className="h-72 w-full" role="img" aria-label="Fund NAV against its benchmark, rebased to 100" />
          {series && !series.benchmark && (
            <p className="mt-2 text-xs text-muted">No benchmark is mapped for this category, so the fund is shown alone.</p>
          )}
          {series && <p className="mt-1 text-xs text-muted">Up to {formatDate(asOf)}. {series.formula}</p>}
        </>
      )}
    </section>
  );
}
