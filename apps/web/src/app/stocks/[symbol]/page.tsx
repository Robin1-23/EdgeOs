import type { Metadata } from "next";
import { citeLabels } from "@/components/Evidence";
import { DevBanner, EmptyState } from "@/components/Notices";
import { PriceChart } from "@/components/PriceChart";
import {
  AgentSections, AskCta, BothSides, ConfidenceBadge, KeyNumbers, OpenQuestions, QuoteCard, ReportGaps, SummaryCard,
} from "@/components/ReportBody";
import { apiGet } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { Envelope, StockPage } from "@/lib/types";

export const revalidate = 300;

const STOCK_AGENTS = ["M1", "M2", "M3", "M4", "M5", "M6", "M7"];

type Props = { params: Promise<{ symbol: string }> };

const load = (symbol: string) => apiGet<StockPage>(`/api/stocks/${encodeURIComponent(symbol)}`);

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { symbol } = await params;
  const page = await load(symbol);
  const name = decodeURIComponent(symbol).toUpperCase();
  if (!page.ok) return { title: name, robots: { index: false } };
  return {
    title: `${page.data.meta?.name ?? page.data.subject} (${page.data.subject}): research summary`,
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

  const { report, agents, subject, as_of, dev_only, meta } = page.data;
  const envelopes = Object.values(agents).flatMap((a) => (a.available ? [a.envelope] : [])) as Envelope[];

  const labels = citeLabels(envelopes);
  return (
    <article className="space-y-6">
      <DevBanner show={dev_only} />
      <header className="flex flex-wrap items-end justify-between gap-6 pb-2">
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="rounded-full bg-accent-soft px-2.5 py-1 font-mono font-medium text-accent">NSE · {subject}</span>
            {(meta?.industry ?? meta?.sector) && <span className="rounded-full bg-white/[0.06] px-2.5 py-1 text-muted">{meta?.industry ?? meta?.sector}</span>}
            <ConfidenceBadge level={report.confidence} reason={report.confidence_reason} />
          </div>
          <h1 className="text-4xl font-semibold sm:text-6xl">{meta?.name ?? subject}</h1>
          <p className="text-sm text-muted">Research report · {formatDate(as_of)}{meta?.isin ? ` · ${meta.isin}` : ""}</p>
        </div>
        {page.data.quote && <QuoteCard quote={page.data.quote} />}
      </header>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="min-w-0 space-y-6">
          <PriceChart symbol={subject} asOf={as_of} />
          <SummaryCard report={report} labels={labels} />
          <BothSides report={report} labels={labels} />
        </div>
        <aside className="space-y-6">
          <KeyNumbers items={report.key_items} />
          <OpenQuestions report={report} labels={labels} />
          <AskCta subject={meta?.name ?? subject} />
        </aside>
      </div>

      <AgentSections ids={STOCK_AGENTS} agents={agents} />
      <ReportGaps gaps={report.gaps} />
    </article>
  );
}
