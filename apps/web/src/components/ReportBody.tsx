import { AgentTabs } from "@/components/AgentTabs";
import { CitedList } from "@/components/Evidence";
import { Icon } from "@/components/Icons";
import { anchorId } from "@/lib/format";
import type { AgentSlot, Report, ReportItem } from "@/lib/types";

/** "Close: Rs 1,001.20" -> ["Close", "₹1,001.20"]: the agents' own formatted text, split for a stat tile. */
function splitItem(text: string): [string, string] {
  const i = text.lastIndexOf(": ");
  if (i < 0) return [text, ""];
  return [text.slice(0, i), text.slice(i + 2).replace(/^Rs -/, "−₹").replace(/^Rs /, "₹")];
}

function KeyItems({ items }: { items: ReportItem[] }) {
  const tiles = items.map((k) => [k, ...splitItem(k.text)] as const).filter(([, , v]) => v && v.length <= 24);
  if (!tiles.length) return null;
  return (
    <ul className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {tiles.slice(0, 4).map(([k, label, value]) => (
        <li key={k.id}>
          <a href={`#${anchorId(k.id)}`} className="card block h-full p-4 transition-colors hover:border-accent/40">
            <span className="line-clamp-2 text-xs text-muted">{label}</span>
            <span className="mt-1 block font-mono text-lg font-semibold tabular-nums">{value}</span>
          </a>
        </li>
      ))}
    </ul>
  );
}

const CONFIDENCE = { high: "bg-up/10 text-up", medium: "bg-watch/10 text-watch", low: "bg-surface-2 text-muted" };

export function ConfidenceBadge({ level, reason }: { level: Report["confidence"]; reason: string }) {
  return (
    <span title={reason} className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium ${CONFIDENCE[level]}`}>
      {level[0].toUpperCase() + level.slice(1)} confidence
    </span>
  );
}

/** The mother agent's synthesis, shared by stock and fund pages: summary, key numbers, both sides, open questions. */
export function ReportSections({ report, labels }: { report: Report; labels: Map<string, string> }) {
  return (
    <>
      <section aria-labelledby="summary-title" className="card p-6">
        <h2 id="summary-title" className="mb-4 flex items-center gap-2 text-lg font-semibold">
          <Icon name="sparkles" className="h-5 w-5 text-accent" /> Summary
        </h2>
        <CitedList items={report.summary} labels={labels} empty="No summary could be published for this report." />
      </section>

      <KeyItems items={report.key_items} />

      <section aria-labelledby="debate-title" className="space-y-4">
        <h2 id="debate-title" className="text-lg font-semibold">Both sides</h2>
        <div className="grid gap-4 md:grid-cols-2">
          <div className="card overflow-hidden">
            <h3 className="flex items-center gap-2 border-b border-line bg-up/5 px-5 py-3 text-sm font-semibold text-up">
              <Icon name="up" /> What supports
            </h3>
            <div className="p-5"><Side items={report.bull} labels={labels} tone="up" /></div>
          </div>
          <div className="card overflow-hidden">
            <h3 className="flex items-center gap-2 border-b border-line bg-down/5 px-5 py-3 text-sm font-semibold text-down">
              <Icon name="down" /> What weighs
            </h3>
            <div className="p-5"><Side items={report.bear} labels={labels} tone="down" /></div>
          </div>
        </div>
      </section>

      {report.risk_notes.length > 0 && (
        <section aria-labelledby="risks-title" className="card p-6">
          <h2 id="risks-title" className="mb-4 flex items-center gap-2 text-lg font-semibold">
            <Icon name="alert" className="h-5 w-5 text-watch" /> Open questions
          </h2>
          <CitedList items={report.risk_notes} labels={labels} />
        </section>
      )}
    </>
  );
}

const SHOWN = 4;

function Side({ items, labels, tone }: { items: Report["bull"]; labels: Map<string, string>; tone: "up" | "down" }) {
  return (
    <>
      <CitedList items={items.slice(0, SHOWN)} labels={labels} tone={tone} empty="Nothing passed verification." />
      {items.length > SHOWN && (
        <details className="mt-3">
          <summary className="cursor-pointer select-none text-sm text-muted hover:text-foreground">Show {items.length - SHOWN} more</summary>
          <div className="mt-3"><CitedList items={items.slice(SHOWN)} labels={labels} tone={tone} /></div>
        </details>
      )}
    </>
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
