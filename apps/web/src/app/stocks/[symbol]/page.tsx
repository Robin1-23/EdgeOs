import type { Metadata } from "next";
import { AGENT_TITLES, AgentSection, citeLabels, CitedList } from "@/components/Evidence";
import { DevBanner, EmptyState } from "@/components/Notices";
import { PriceChart } from "@/components/PriceChart";
import { apiGet } from "@/lib/api";
import { anchorId, formatDate } from "@/lib/format";
import type { Envelope, StockPage } from "@/lib/types";

export const revalidate = 300;

type Props = { params: Promise<{ symbol: string }> };

const load = (symbol: string) => apiGet<StockPage>(`/api/stocks/${encodeURIComponent(symbol)}`);

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { symbol } = await params;
  const page = await load(symbol);
  const name = decodeURIComponent(symbol).toUpperCase();
  if (!page.ok) return { title: name, robots: { index: false } };
  return {
    title: `${page.data.subject}: research summary`,
    description: page.data.report.summary.map((s) => s.text).join(" ").slice(0, 300),
    robots: page.data.dev_only ? { index: false } : undefined, // dev data is never indexed
  };
}

function emptyState(status: number, detail: string) {
  if (detail.includes("licence")) return <EmptyState title="Not available" detail="This page uses data that is not licensed for public display yet." />;
  if (detail.startsWith("no single stock")) return <EmptyState title="Not covered" detail="We could not find one NSE-listed company with that symbol." />;
  if (detail.startsWith("no published report")) return <EmptyState title="No report yet" detail="This company is covered, but tonight's report has not been published yet." />;
  return <EmptyState title="Something went wrong" detail={`The research service answered: ${detail} (${status}).`} />;
}

export default async function StockPageView({ params }: Props) {
  const { symbol } = await params;
  const page = await load(symbol);
  if (!page.ok) return emptyState(page.status, page.detail);

  const { report, agents, subject, as_of, dev_only } = page.data;
  const envelopes = Object.values(agents).flatMap((a) => (a.available ? [a.envelope] : [])) as Envelope[];
  const labels = citeLabels(envelopes);

  return (
    <article className="space-y-8">
      <DevBanner show={dev_only} />
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">{subject}</h1>
        <p className="mt-1 text-sm text-muted">
          Report for {formatDate(as_of)} · confidence {report.confidence}: {report.confidence_reason}
        </p>
      </header>

      <PriceChart symbol={subject} asOf={as_of} />

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

      <section aria-labelledby="detail-title" className="space-y-4">
        <h2 id="detail-title" className="text-lg font-semibold">The evidence, agent by agent</h2>
        {Object.entries(AGENT_TITLES).map(([id, title]) => {
          const slot = agents[id];
          return (
            <details key={id} open className="rounded-lg border border-line">
              <summary className="cursor-pointer px-4 py-3 font-medium">{title}</summary>
              <div className="border-t border-line px-4 py-3">
                {!slot ? (
                  <p className="text-sm text-muted">Not part of this report.</p>
                ) : slot.available ? (
                  <AgentSection envelope={slot.envelope} />
                ) : (
                  <p className="text-sm text-muted">Not available: {slot.reason}.</p>
                )}
              </div>
            </details>
          );
        })}
      </section>

      {report.gaps.length > 0 && (
        <section aria-labelledby="gaps-title" className="text-xs text-muted">
          <h2 id="gaps-title" className="font-medium">What this report could not cover</h2>
          <ul className="mt-1 space-y-0.5">
            {report.gaps.map((g, i) => (
              <li key={i}>{g}</li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}
