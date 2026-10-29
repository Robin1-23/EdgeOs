import type { Metadata } from "next";
import { citeLabels } from "@/components/Evidence";
import { DevBanner, EmptyState } from "@/components/Notices";
import { PriceChart } from "@/components/PriceChart";
import { AgentSections, ReportGaps, ReportSections } from "@/components/ReportBody";
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

  return (
    <article className="space-y-8">
      <DevBanner show={dev_only} />
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">{meta?.name ?? subject}</h1>
        <p className="mt-1 text-sm text-muted">
          {[meta?.name ? `NSE: ${subject}` : null, meta?.isin, meta?.industry ?? meta?.sector].filter(Boolean).join(" · ")}
        </p>
        <p className="mt-1 text-sm text-muted">
          Report for {formatDate(as_of)} · confidence {report.confidence}: {report.confidence_reason}
        </p>
      </header>

      <PriceChart symbol={subject} asOf={as_of} />
      <ReportSections report={report} labels={citeLabels(envelopes)} />
      <AgentSections ids={STOCK_AGENTS} agents={agents} />
      <ReportGaps gaps={report.gaps} />
    </article>
  );
}
