"use client";

import { CandlestickSeries, ColorType, createChart, HistogramSeries } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { formatDate, monthsBefore } from "@/lib/format";
import type { Bar } from "@/lib/types";

const RANGES = [
  { label: "6M", months: 6 },
  { label: "1Y", months: 12 },
  { label: "2Y", months: 24 },
  { label: "5Y", months: 60 },
];

function css(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Daily NSE candles and volume up to the report date. Prices are as traded (not adjusted for splits or bonuses). */
export function PriceChart({ symbol, asOf }: { symbol: string; asOf: string }) {
  const box = useRef<HTMLDivElement>(null);
  const [months, setMonths] = useState(12);
  const [bars, setBars] = useState<Bar[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    setError(null);
    fetch(`/api/stocks/${encodeURIComponent(symbol)}/prices?from=${monthsBefore(asOf, months)}`, { signal: ctl.signal })
      .then(async (r) => {
        const body = await r.json();
        if (!r.ok) throw new Error(body.detail ?? "prices not available");
        setBars((body.bars as Bar[]).filter((b) => b.date <= asOf));
      })
      .catch((e: Error) => e.name !== "AbortError" && setError(e.message));
    return () => ctl.abort();
  }, [symbol, asOf, months]);

  useEffect(() => {
    if (!box.current || !bars?.length) return;
    const chart = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: css("--muted"), attributionLogo: true },
      grid: { vertLines: { color: css("--line") }, horzLines: { color: css("--line") } },
      rightPriceScale: { borderColor: css("--line") },
      timeScale: { borderColor: css("--line") },
    });
    const up = css("--up");
    const down = css("--down");
    chart
      .addSeries(CandlestickSeries, { upColor: up, downColor: down, borderVisible: false, wickUpColor: up, wickDownColor: down })
      .setData(bars.map((b) => ({ time: b.date, open: b.open, high: b.high, low: b.low, close: b.close })));
    const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "", color: css("--muted") });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    volume.setData(bars.map((b) => ({ time: b.date, value: b.volume, color: b.close >= b.open ? `${up}66` : `${down}66` })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars]);

  return (
    <section aria-labelledby="chart-title" className="card p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h2 id="chart-title" className="font-semibold">
          Price <span className="ml-1 text-xs font-normal text-muted" title="Daily NSE prices, not adjusted for splits or bonuses">NSE · daily · unadjusted</span>
        </h2>
        <div role="group" aria-label="Chart range" className="flex rounded-full bg-surface-2 p-1">
          {RANGES.map((r) => (
            <button
              key={r.label}
              type="button"
              aria-pressed={months === r.months}
              onClick={() => setMonths(r.months)}
              className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${months === r.months ? "bg-surface text-foreground shadow-sm" : "text-muted hover:text-foreground"}`}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>
      {error ? (
        <p className="py-10 text-center text-sm text-muted">Chart not available: {error}</p>
      ) : bars && bars.length === 0 ? (
        <p className="py-10 text-center text-sm text-muted">No trades in this range up to {formatDate(asOf)}.</p>
      ) : (
        <div ref={box} className="h-72 w-full" aria-label={`Daily price chart for ${symbol}`} role="img" />
      )}
    </section>
  );
}
