import type { Metadata } from "next";
import { OverlapTool } from "./OverlapTool";

export const metadata: Metadata = {
  title: "Fund overlap",
  description: "How much two or more mutual funds hold in common, their combined exposure and the regular-plan cost gap.",
};

type Props = { searchParams: Promise<{ f?: string }> };

export default async function OverlapPage({ searchParams }: Props) {
  const { f } = await searchParams;
  const codes = (f ?? "").split(",").filter((c) => /^\d+$/.test(c)).slice(0, 20);
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Fund overlap</h1>
        <p className="mt-1 max-w-2xl text-muted">
          Pick two or more schemes to see how much of their portfolios they share. Add the amount in each to see your
          combined stock and sector exposure. Amounts stay in this page; they are not saved or put in the link.
        </p>
      </header>
      <OverlapTool initialCodes={codes} />
    </div>
  );
}
