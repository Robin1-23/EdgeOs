import Link from "next/link";
import { AgentTabs } from "@/components/AgentTabs";
import { CitedList } from "@/components/Evidence";
import { Icon, type IconName } from "@/components/Icons";
import { anchorId, formatChange, formatDate, formatValue } from "@/lib/format";
import type { AgentSlot, Quote, Report, ReportItem } from "@/lib/types";

/** "Close: Rs 1,001.20" -> ["Close", "₹1,001.20"]: the agents' own formatted text, split for display. */
function splitItem(text: string): [string, string] {
  const i = text.lastIndexOf(": ");
  if (i < 0) return [text, ""];
  return [text.slice(0, i), text.slice(i + 2).replace(/^Rs -/, "−₹").replace(/^Rs /, "₹")];
}

const CONFIDENCE = { high: "bg-up/10 text-up", medium: "bg-watch/10 text-watch", low: "bg-white/[0.06] text-muted" };

export function ConfidenceBadge({ level, reason }: { level: Report["confidence"]; reason: string }) {
  return (
    <span title={reason} className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${CONFIDENCE[level]}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {level[0].toUpperCase() + level.slice(1)} confidence
    </span>
  );
}

function CardTitle({ icon, id, children, tone = "text-accent" }: { icon: IconName; id?: string; children: React.ReactNode; tone?: string }) {
  return (
    <h2 id={id} className="mb-5 flex items-center gap-2.5 text-base font-semibold">
      <span className={`flex h-7 w-7 items-center justify-center rounded-full bg-white/[0.05] ${tone}`}>
        <Icon name={icon} className="h-3.5 w-3.5" />
      </span>
      {children}
    </h2>
  );
}

/** The latest close as the page's headline number, with its day change and date (both from the API). */
export function QuoteCard({ quote }: { quote: Quote }) {
  const up = (quote.change ?? 0) >= 0;
  return (
    <div className="card min-w-[16rem] px-6 py-5" title={quote.formula}>
      <p className="text-xs text-muted">Last close · {formatDate(quote.date)}</p>
      <p className="mt-2 flex items-baseline gap-3">
        <span className="font-display text-4xl font-semibold tabular-nums tracking-tight">{formatValue(quote.close, "INR")}</span>
        {quote.change !== null && (
          <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-mono text-xs tabular-nums ${up ? "bg-up/10 text-up" : "bg-down/10 text-down"}`}>
            <Icon name={up ? "up" : "down"} className="h-3 w-3" />
            {formatChange(quote.change)}
          </span>
        )}
      </p>
    </div>
  );
}

export function SummaryCard({ report, labels }: { report: Report; labels: Map<string, string> }) {
  return (
    <section aria-labelledby="summary-title" className="card p-6">
      <CardTitle icon="sparkles" id="summary-title">Summary</CardTitle>
      <CitedList items={report.summary} labels={labels} empty="No summary could be published for this report." />
    </section>
  );
}

const SHOWN = 4;

function Side({ items, labels, tone }: { items: Report["bull"]; labels: Map<string, string>; tone: "up" | "down" }) {
  return (
    <>
      <CitedList items={items.slice(0, SHOWN)} labels={labels} tone={tone} empty="Nothing passed verification." />
      {items.length > SHOWN && (
        <details className="mt-4">
          <summary className="cursor-pointer select-none text-sm text-muted hover:text-foreground">Show {items.length - SHOWN} more</summary>
          <div className="mt-3"><CitedList items={items.slice(SHOWN)} labels={labels} tone={tone} /></div>
        </details>
      )}
    </>
  );
}

export function BothSides({ report, labels }: { report: Report; labels: Map<string, string> }) {
  return (
    <section aria-label="What supports and what weighs" className="grid gap-4 md:grid-cols-2">
      <div className="card p-6">
        <CardTitle icon="up" tone="text-up">What supports</CardTitle>
        <Side items={report.bull} labels={labels} tone="up" />
      </div>
      <div className="card p-6">
        <CardTitle icon="down" tone="text-down">What weighs</CardTitle>
        <Side items={report.bear} labels={labels} tone="down" />
      </div>
    </section>
  );
}

/** Key numbers as a compact list, like a watchlist; each row jumps to its evidence. */
export function KeyNumbers({ items }: { items: ReportItem[] }) {
  const rows = items.map((k) => [k, ...splitItem(k.text)] as const).filter(([, , v]) => v && v.length <= 24).slice(0, 6);
  if (!rows.length) return null;
  return (
    <section aria-labelledby="key-title" className="card p-3">
      <h2 id="key-title" className="px-3 pb-2 pt-3 text-[11px] font-medium uppercase tracking-[0.14em] text-muted">Key numbers</h2>
      <ul>
        {rows.map(([k, label, value]) => (
          <li key={k.id}>
            <a href={`#${anchorId(k.id)}`} className="flex items-center justify-between gap-4 rounded-2xl px-3 py-3 transition-colors hover:bg-white/[0.04]">
              <span className="truncate text-sm text-muted">{label}</span>
              <span className="shrink-0 font-mono text-sm font-medium tabular-nums">{value}</span>
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function OpenQuestions({ report, labels }: { report: Report; labels: Map<string, string> }) {
  if (!report.risk_notes.length) return null;
  return (
    <section aria-labelledby="risks-title" className="card p-6">
      <CardTitle icon="alert" id="risks-title" tone="text-watch">Open questions</CardTitle>
      <CitedList items={report.risk_notes.slice(0, 3)} labels={labels} />
    </section>
  );
}

export function AskCta({ subject }: { subject: string }) {
  return (
    <Link href="/ask" className="group relative block overflow-hidden rounded-[1.25rem] bg-accent p-6 text-accent-ink">
      <span className="absolute -right-8 -top-8 h-32 w-32 rounded-full bg-white/30 blur-2xl" aria-hidden />
      <Icon name="sparkles" className="h-6 w-6" />
      <p className="mt-4 font-display text-xl font-semibold leading-tight">Have a question about {subject}?</p>
      <p className="mt-1 text-sm opacity-75">Ask EdgeOS. Every answer shows its sources.</p>
      <span className="mt-5 inline-flex items-center gap-1.5 text-sm font-semibold">
        Ask now <Icon name="arrow" className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
      </span>
    </Link>
  );
}

export function AgentSections(props: { ids: string[]; agents: Record<string, AgentSlot>; extra?: Record<string, React.ReactNode> }) {
  return <AgentTabs {...props} />;
}

export function ReportGaps({ gaps }: { gaps: string[] }) {
  if (!gaps.length) return null;
  return (
    <details className="text-xs text-muted">
      <summary className="cursor-pointer select-none hover:text-foreground">What this report could not cover ({gaps.length})</summary>
      <ul className="mt-2 space-y-1 border-l border-line pl-3">
        {gaps.map((g, i) => (
          <li key={i}>{g}</li>
        ))}
      </ul>
    </details>
  );
}
