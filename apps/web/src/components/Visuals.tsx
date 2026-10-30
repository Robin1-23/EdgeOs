// Visual cards drawn from agent metrics. Positions are drawing only; every number shown is a metric the agent computed.
import { Icon, type IconName } from "@/components/Icons";
import { anchorId, formatValue } from "@/lib/format";
import type { AgentSlot, Envelope, Metric } from "@/lib/types";

const num = (m?: Metric) => (m && typeof m.value === "number" && Number.isFinite(m.value) ? m.value : null);
const byId = (e: Envelope) => new Map(e.metrics.map((m) => [m.id, m]));
const clamp01 = (x: number) => Math.min(1, Math.max(0, x));

function VisualCard({ icon, title, href, children }: { icon: IconName; title: string; href: string; children: React.ReactNode }) {
  return (
    <a href={href} className="card card-hover group block p-5">
      <p className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.12em] text-muted">
        <Icon name={icon} className="h-3.5 w-3.5 text-accent" />
        {title}
      </p>
      <div className="mt-4">{children}</div>
    </a>
  );
}

/** A low-high track with a marker; optional median tick. */
export function RangeBar({ low, high, value, median }: { low: Metric; high: Metric; value: Metric; median?: Metric }) {
  const [lo, hi, v] = [num(low), num(high), num(value)];
  if (lo === null || hi === null || v === null || hi <= lo) return null;
  const at = clamp01((v - lo) / (hi - lo));
  const med = num(median);
  return (
    <div>
      <div className="relative h-2 rounded-full bg-white/[0.08]">
        {med !== null && <span className="absolute top-1/2 h-3 w-px -translate-y-1/2 bg-white/50" style={{ left: `${clamp01((med - lo) / (hi - lo)) * 100}%` }} title={`Median ${formatValue(med, median!.unit)}`} />}
        <span className="absolute top-1/2 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-background bg-accent shadow-[0_0_12px_var(--accent)]" style={{ left: `${at * 100}%` }} />
      </div>
      <div className="mt-3 flex justify-between font-mono text-xs tabular-nums text-muted">
        <span>{formatValue(lo, low.unit)}</span>
        <span className="text-foreground">{formatValue(v, value.unit)}</span>
        <span>{formatValue(hi, high.unit)}</span>
      </div>
    </div>
  );
}

export function FiftyTwoWeek({ m1 }: { m1: Envelope }) {
  const m = byId(m1);
  const [low, high, close] = [m.get("low_52w"), m.get("high_52w"), m.get("close")];
  if (!low || !high || !close) return null;
  return (
    <VisualCard icon="chart" title="52-week range" href={`#${anchorId("M1:high_52w")}`}>
      <RangeBar low={low} high={high} value={close} />
      <p className="mt-3 text-xs text-muted">Low · close · high</p>
    </VisualCard>
  );
}

/** Semicircle gauge: red-flag score out of 100, with the share of check weight that could be evaluated. */
export function RedFlagGauge({ m3 }: { m3: Envelope }) {
  const m = byId(m3);
  const [score, evaluated] = [num(m.get("red_flag_score")), num(m.get("evaluated_weight"))];
  if (score === null || evaluated === null) return null;
  const arc = (frac: number) => {
    const a = Math.PI * (1 - clamp01(frac));
    return `M10,50 A40,40 0 0 1 ${50 + 40 * Math.cos(a)},${50 - 40 * Math.sin(a)}`;
  };
  const tone = score >= 40 ? "var(--down)" : score >= 20 ? "var(--watch)" : "var(--up)";
  return (
    <VisualCard icon="shield" title="Red-flag score" href={`#${anchorId("M3:red_flag_score")}`}>
      <div className="flex items-end gap-3">
        <svg viewBox="0 0 100 56" className="h-14 w-24 shrink-0" aria-hidden>
          <path d={arc(1)} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="9" strokeLinecap="round" />
          {evaluated > 0 && <path d={arc(evaluated / 100)} fill="none" stroke="rgba(255,255,255,0.18)" strokeWidth="9" strokeLinecap="round" />}
          {score > 0 && <path d={arc(score / 100)} fill="none" stroke={tone} strokeWidth="9" strokeLinecap="round" />}
        </svg>
        <div className="min-w-0">
          <p className="font-display text-3xl font-semibold tabular-nums">{formatValue(score, null)}<span className="text-base text-muted"> / 100</span></p>
          <p className="text-xs text-muted">{formatValue(evaluated, null)} of 100 points checked</p>
        </div>
      </div>
    </VisualCard>
  );
}

const MULTIPLES: [string, string][] = [["pe", "P/E"], ["pb", "P/B"], ["ev_ebitda", "EV/EBITDA"]];

export function ValuationBands({ m7 }: { m7: Envelope }) {
  const m = byId(m7);
  const rows = MULTIPLES.filter(([k]) => m.get(k) && m.get(`${k}_hist_min`) && m.get(`${k}_hist_max`));
  if (!rows.length) return null;
  return (
    <VisualCard icon="scale" title="Valuation vs 10-year history" href={`#${anchorId(`M7:${rows[0][0]}`)}`}>
      <div className="space-y-5">
        {rows.map(([k, label]) => (
          <div key={k}>
            <p className="mb-2 text-xs text-muted">{label}</p>
            <RangeBar low={m.get(`${k}_hist_min`)!} high={m.get(`${k}_hist_max`)!} value={m.get(k)!} median={m.get(`${k}_hist_median`)} />
          </div>
        ))}
      </div>
    </VisualCard>
  );
}

export function PeerBars({ m6 }: { m6: Envelope }) {
  const rows = m6.metrics.filter((x) => x.id.endsWith("_percentile") && num(x) !== null);
  if (!rows.length) return null;
  return (
    <VisualCard icon="users" title="Position among peers" href={`#${anchorId(`M6:${rows[0].id}`)}`}>
      <ul className="space-y-3">
        {rows.map((x) => (
          <li key={x.id}>
            <div className="flex justify-between text-xs">
              <span className="text-muted">{x.name.replace(/^Share of peers with a lower /, "")}</span>
              <span className="font-mono tabular-nums">{formatValue(num(x)!, "ratio")}</span>
            </div>
            <div className="mt-1.5 h-1.5 rounded-full bg-white/[0.07]">
              <div className="h-full rounded-full bg-accent" style={{ width: `${clamp01(num(x)!) * 100}%` }} />
            </div>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs text-muted">Share of industry peers below the company; higher is not always better.</p>
    </VisualCard>
  );
}

/** Whichever visual cards the report's agents can fill, as one row. */
export function AtAGlance({ agents }: { agents: Record<string, AgentSlot> }) {
  const env = (id: string) => (agents[id]?.available ? (agents[id] as { envelope: Envelope }).envelope : null);
  const cards = [
    env("M1") && <FiftyTwoWeek key="m1" m1={env("M1")!} />,
    env("M3") && <RedFlagGauge key="m3" m3={env("M3")!} />,
    env("M7") && <ValuationBands key="m7" m7={env("M7")!} />,
    env("M6") && <PeerBars key="m6" m6={env("M6")!} />,
  ].filter(Boolean);
  if (!cards.length) return null;
  return (
    <section aria-label="At a glance" className="grid gap-4 sm:grid-cols-2">
      {cards}
    </section>
  );
}
