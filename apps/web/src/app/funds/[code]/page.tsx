import type { Metadata } from "next";
import Link from "next/link";
import { citeLabels } from "@/components/Evidence";
import { FundChart } from "@/components/FundChart";
import { DevBanner, EmptyState } from "@/components/Notices";
import { AgentSections, ReportGaps, ReportSections } from "@/components/ReportBody";
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
    <table className="w-full max-w-lg text-sm">
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
    <article className="space-y-8">
      <DevBanner show={dev_only} />
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">{meta?.name ?? `AMFI ${subject}`}</h1>
        <p className="mt-1 text-sm text-muted">
          {[meta?.amc, meta?.category, [meta?.plan, meta?.option].filter(Boolean).join(", "), `AMFI ${subject}`].filter(Boolean).join(" · ")}
        </p>
        <p className="mt-1 text-sm text-muted">
          Report for {formatDate(as_of)} · confidence {report.confidence}: {report.confidence_reason}
        </p>
        <Link href={`/tools/overlap?f=${subject}`} className="mt-2 inline-block text-sm text-accent underline underline-offset-4">
          Compare its portfolio with other funds
        </Link>
      </header>

      <FundChart code={subject} asOf={as_of} />
      <ReportSections report={report} labels={citeLabels(envelopes)} />
      <AgentSections ids={FUND_AGENTS} agents={agents} extra={mf2 ? { MF2: <ReturnsTable mf2={mf2} /> } : undefined} />
      <ReportGaps gaps={report.gaps} />
    </article>
  );
}
