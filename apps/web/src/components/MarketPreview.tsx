"use client";

import { AreaSeries, ColorType, createChart, LineStyle } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/Icons";
import { Sparkline } from "@/components/Sparkline";
import { formatChange, formatDate, formatValue, monthsBefore } from "@/lib/format";
import type { Home } from "@/lib/types";

const RANGES = [
  { label: "1M", months: 1 },
  { label: "3M", months: 3 },
  { label: "1Y", months: 12 },
  { label: "5Y", months: 60 },
];

function css(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** The home page's live panel: one index charted, every headline index listed with its trend; pick a row to chart it. */
export function MarketPreview({ home }: { home: Home }) {
  const [name, setName] = useState(home.indices[0]?.name);
  const [months, setMonths] = useState(12);
  const [points, setPoints] = useState<{ date: string; close: number }[] | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const selected = home.indices.find((i) => i.name === name);
  const asOf = selected?.date;

  useEffect(() => {
    if (!name || !asOf) return;
    const ctl = new AbortController();
    fetch(`/api/indices/${encodeURIComponent(name)}?from=${monthsBefore(asOf, months)}&to=${asOf}`, { signal: ctl.signal })
      .then((r) => (r.ok ? r.json() : { points: [] }))
      .then((b) => setPoints(b.points))
      .catch(() => {});
    return () => ctl.abort();
  }, [name, asOf, months]);

  useEffect(() => {
    if (!box.current || !points?.length) return;
    const accent = css("--accent");
    const chart = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: css("--muted"), attributionLogo: true, fontFamily: "var(--font-geist-mono)" },
      grid: { vertLines: { visible: false }, horzLines: { color: "rgba(255,255,255,0.05)", style: LineStyle.Dashed } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
      crosshair: { vertLine: { color: "rgba(198,241,53,0.4)" }, horzLine: { color: "rgba(198,241,53,0.4)" } },
      handleScroll: false,
      handleScale: false,
    });
    chart
      .addSeries(AreaSeries, { lineColor: accent, lineWidth: 2, topColor: "rgba(198,241,53,0.28)", bottomColor: "rgba(198,241,53,0)", priceLineVisible: false })
      .setData(points.map((p) => ({ time: p.date, value: p.close })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [points]);

  if (!selected) return null;
  const up = (selected.change ?? 0) >= 0;
  const b = home.breadth;

  return (
    <section id="markets" aria-labelledby="markets-title" className="card overflow-hidden lg:grid lg:grid-cols-[1fr_22rem]">
      <div className="border-b border-line p-6 lg:border-b-0 lg:border-r">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 id="markets-title" className="flex items-center gap-2 text-sm font-medium text-muted">
              <span className="flex h-6 w-6 items-center justify-center rounded-full bg-accent-soft text-accent"><Icon name="chart" className="h-3.5 w-3.5" /></span>
              {selected.name}
              <span className="text-xs">· close {formatDate(selected.date)}</span>
            </h2>
            <p className="mt-3 flex flex-wrap items-baseline gap-3">
              <span className="font-display text-4xl font-semibold tabular-nums tracking-tight sm:text-5xl">{formatValue(selected.close, null)}</span>
              {selected.change !== null && (
                <span className={`rounded-full px-2.5 py-1 font-mono text-sm tabular-nums ${up ? "bg-up/10 text-up" : "bg-down/10 text-down"}`}>
                  {formatChange(selected.change)}
                </span>
              )}
            </p>
          </div>
          <div role="group" aria-label="Chart range" className="flex rounded-full border border-line p-1">
            {RANGES.map((r) => (
              <button
                key={r.label}
                type="button"
                aria-pressed={months === r.months}
                onClick={() => setMonths(r.months)}
                className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${months === r.months ? "bg-accent text-accent-ink" : "text-muted hover:text-foreground"}`}
              >
                {r.label}
              </button>
            ))}
          </div>
        </div>
        <div ref={box} className="mt-6 h-64 w-full sm:h-72" role="img" aria-label={`${selected.name} closing levels`} />
        {b && (
          <div className="mt-5" title={b.formula}>
            <div className="flex justify-between text-xs">
              <span className="text-up">{b.advances.toLocaleString("en-IN")} up</span>
              <span className="text-muted">NSE breadth · {formatDate(b.date)}</span>
              <span className="text-down">{b.declines.toLocaleString("en-IN")} down</span>
            </div>
            <div className="mt-2 flex h-1.5 gap-1" aria-hidden>
              <div className="rounded-full bg-up" style={{ flex: b.advances }} />
              <div className="rounded-full bg-muted/30" style={{ flex: b.unchanged }} />
              <div className="rounded-full bg-down" style={{ flex: b.declines }} />
            </div>
          </div>
        )}
      </div>
      <div className="p-3">
        <p className="px-3 pb-2 pt-3 text-[11px] font-medium uppercase tracking-[0.14em] text-muted">Markets</p>
        <ul>
          {home.indices.map((i) => {
            const rowUp = (i.change ?? 0) >= 0;
            return (
              <li key={i.name}>
                <button
                  type="button"
                  onClick={() => setName(i.name)}
                  aria-pressed={i.name === name}
                  className={`grid w-full grid-cols-[1fr_4.5rem_auto] items-center gap-3 rounded-2xl px-3 py-3 text-left transition-colors ${i.name === name ? "bg-white/[0.05]" : "hover:bg-white/[0.03]"}`}
                >
                  <span className="truncate text-sm font-medium">{i.name}</span>
                  <Sparkline values={i.spark} up={rowUp} className="h-7 w-full" />
                  <span className="text-right">
                    <span className="block font-mono text-sm tabular-nums">{formatValue(i.close, null)}</span>
                    <span className={`block font-mono text-xs tabular-nums ${rowUp ? "text-up" : "text-down"}`}>{formatChange(i.change)}</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}
