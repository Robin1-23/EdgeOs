import type { Metadata } from "next";
import Link from "next/link";
import { citeLabels } from "@/components/Evidence";
import { FundChart } from "@/components/FundChart";
import { DevBanner, EmptyState } from "@/components/Notices";
import { Icon } from "@/components/Icons";
import {
  AgentSections, AskCta, BothSides, ConfidenceBadge, KeyNumbers, OpenQuestions, ReportGaps, SummaryCard,
} from "@/components/ReportBody";
import { apiGet } from "@/lib/api";
import { anchorId, formatDate, formatValue } from "@/lib/format";
import { returnsRows } from "@/lib/funds";
import type { Envelope, FundPage, Metric } from "@/lib/types";

export const revalidate = 300;

const FUND_AGENTS = ["MF2", "MF1", "MF3"];

type Props = { params: Promise<{ code: string }> };

const load = (code: string) => apiGet<FundPage>(`/api/funds/${encodeURIComponent(code)}`);

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { code } = await params;
  const page = await load(code);
  if (!page.ok) return { title: `Fund ${code}`, robots: { index: false } };
  return {
    title: `${page.data.meta?.name ?? `AMFI ${code}`}: fund research`,
    description: page.data.report.summary.map((s) => s.text).join(" ").slice(0, 300),
    robots: page.data.dev_only ? { index: false } : undefined,
  };
}

function emptyState(status: number, detail: string) {
  if (detail.includes("licence")) return <EmptyState title="Not available" detail="This page uses data that is not licensed for public display yet." />;
  if (detail.startsWith("no fund")) return <EmptyState title="Not covered" detail="We could not find a scheme with that AMFI code." />;
  if (detail.startsWith("no published report")) return <EmptyState title="No report yet" detail="This scheme is covered, but tonight's report has not been published yet." />;
  return <EmptyState title="Something went wrong" detail={`The research service answered: ${detail} (${status}).`} />;
}

function Cell({ m }: { m?: Metric }) {
  return <td className="py-1.5 pr-3 text-right font-mono tabular-nums">{m ? <a href={`#${anchorId(`MF2:${m.id}`)}`}>{formatValue(m.value, m.unit)}</a> : "—"}</td>;
}

/** MF2's returns beside its category averages; each value links to its row (formula and source) below. */
function ReturnsTable({ mf2 }: { mf2: Envelope }) {
  const rows = returnsRows(mf2);
  if (!rows.length) return null;
  return (
    <table className="w-full max-w-xl text-sm">
      <caption className="mb-1 text-left text-xs text-muted">Returns, annualised beyond one year (growth option NAV)</caption>
      <thead>
        <tr className="border-b border-line text-xs text-muted">
          <th className="py-1 text-left font-normal">Period</th>
          <th className="py-1 pr-3 text-right font-normal">This fund</th>
          <th className="py-1 pr-3 text-right font-normal">Category average</th>
          <th className="py-1 pr-3 text-right font-normal">Difference</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.years} className="border-b border-line last:border-0">
            <th scope="row" className="py-1.5 text-left font-normal">{r.years} year{r.years > 1 ? "s" : ""}</th>
            <Cell m={r.fund} />
            <Cell m={r.category} />
            <Cell m={r.difference} />
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default async function FundPageView({ params }: Props) {
  const { code } = await params;
  const page = await load(code);
  if (!page.ok) return emptyState(page.status, page.detail);

  const { report, agents, subject, as_of, dev_only, meta } = page.data;
  const envelopes = Object.values(agents).flatMap((a) => (a.available ? [a.envelope] : [])) as Envelope[];
  const mf2 = agents.MF2?.available ? agents.MF2.envelope : null;

  return (
    <article className="space-y-6">
      <DevBanner show={dev_only} />
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="rounded-full bg-accent-soft px-2.5 py-1 font-mono font-medium text-accent">Mutual fund · {subject}</span>
            {meta?.plan && <span className="rounded-full bg-surface-2 px-2.5 py-1 capitalize text-muted">{[meta.plan, meta.option].filter(Boolean).join(" · ")}</span>}
            <ConfidenceBadge level={report.confidence} reason={report.confidence_reason} />
          </div>
          <h1 className="text-4xl font-semibold sm:text-5xl">{meta?.name ?? `AMFI ${subject}`}</h1>
          <p className="text-sm text-muted">{[meta?.amc, `Report for ${formatDate(as_of)}`].filter(Boolean).join(" · ")}</p>
        </div>
        <Link href={`/tools/overlap?f=${subject}`} className="btn-ghost px-4 py-2 text-sm">
          <Icon name="layers" /> Compare overlap
        </Link>
      </header>

      <div className="rise grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]" style={{ "--d": "120ms" } as React.CSSProperties}>
        <div className="min-w-0 space-y-6">
          <FundChart code={subject} asOf={as_of} />
          <SummaryCard report={report} labels={citeLabels(envelopes)} />
          <BothSides report={report} labels={citeLabels(envelopes)} />
        </div>
        <aside className="space-y-6">
          <KeyNumbers items={report.key_items} />
          <OpenQuestions report={report} labels={citeLabels(envelopes)} />
          <AskCta subject={meta?.name ?? `this fund`} />
        </aside>
      </div>
      <AgentSections ids={FUND_AGENTS} agents={agents} extra={mf2 ? { MF2: <ReturnsTable mf2={mf2} /> } : undefined} />
      <ReportGaps gaps={report.gaps} />
    </article>
  );
}
