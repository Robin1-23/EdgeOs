import { AGENT_TITLES, AgentSection, CitedList } from "@/components/Evidence";
import { anchorId } from "@/lib/format";
import type { AgentSlot, Report } from "@/lib/types";

/** The mother agent's synthesis, shared by stock and fund pages: summary, key items, debate and risks. */
export function ReportSections({ report, labels }: { report: Report; labels: Map<string, string> }) {
  return (
    <>
      <section aria-labelledby="summary-title" className="space-y-3">
        <h2 id="summary-title" className="text-lg font-semibold">Summary</h2>
        <CitedList items={report.summary} labels={labels} empty="No summary could be published for this report." />
        {report.key_items.length > 0 && (
          <ul className="grid gap-2 pt-2 sm:grid-cols-2">
            {report.key_items.map((k) => (
              <li key={k.id} className="rounded border border-line px-3 py-2 text-sm">
                <a href={`#${anchorId(k.id)}`} className="hover:text-accent">{k.text}</a>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="debate-title">
        <h2 id="debate-title" className="mb-3 text-lg font-semibold">What supports and what weighs</h2>
        <div className="grid gap-4 md:grid-cols-2">
          <div className="rounded-lg border border-line p-4">
            <h3 className="mb-2 text-sm font-semibold">Supportive points</h3>
            <CitedList items={report.bull} labels={labels} empty="No supportive point passed verification." />
          </div>
          <div className="rounded-lg border border-line p-4">
            <h3 className="mb-2 text-sm font-semibold">Concerns</h3>
            <CitedList items={report.bear} labels={labels} empty="No concern passed verification." />
          </div>
        </div>
      </section>

      {(report.risk_notes.length > 0 || report.risks.length > 0) && (
        <section aria-labelledby="risks-title" className="space-y-3">
          <h2 id="risks-title" className="text-lg font-semibold">Risks and open questions</h2>
          <CitedList items={report.risk_notes} labels={labels} />
          {report.risks.length > 0 && (
            <ul className="space-y-1 text-sm">
              {report.risks.map((r) => (
                <li key={r.id}>
                  <a href={`#${anchorId(r.id)}`} className="hover:text-accent">{r.text}</a>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </>
  );
}

/** One collapsible section per agent, in `ids` order; `extra` renders above an agent's generic tables. */
export function AgentSections({ ids, agents, extra }: { ids: string[]; agents: Record<string, AgentSlot>; extra?: Record<string, React.ReactNode> }) {
  return (
    <section aria-labelledby="detail-title" className="space-y-4">
      <h2 id="detail-title" className="text-lg font-semibold">The evidence, agent by agent</h2>
      {ids.map((id) => {
        const slot = agents[id];
        return (
          <details key={id} open className="rounded-lg border border-line">
            <summary className="cursor-pointer px-4 py-3 font-medium">{AGENT_TITLES[id] ?? id}</summary>
            <div className="space-y-4 border-t border-line px-4 py-3">
              {!slot ? (
                <p className="text-sm text-muted">Not part of this report.</p>
              ) : slot.available ? (
                <>
                  {extra?.[id]}
                  <AgentSection envelope={slot.envelope} />
                </>
              ) : (
                <p className="text-sm text-muted">Not available: {slot.reason}.</p>
              )}
            </div>
          </details>
        );
      })}
    </section>
  );
}

export function ReportGaps({ gaps }: { gaps: string[] }) {
  if (!gaps.length) return null;
  return (
    <section aria-labelledby="gaps-title" className="text-xs text-muted">
      <h2 id="gaps-title" className="font-medium">What this report could not cover</h2>
      <ul className="mt-1 space-y-0.5">
        {gaps.map((g, i) => (
          <li key={i}>{g}</li>
        ))}
      </ul>
    </section>
  );
}
