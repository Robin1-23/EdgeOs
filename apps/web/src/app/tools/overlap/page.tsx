import type { Metadata } from "next";
import { Icon } from "@/components/Icons";
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
    <div className="mx-auto max-w-3xl space-y-8 pt-6 sm:pt-10">
      <header className="text-center">
        <span className="mx-auto mb-6 flex h-12 w-12 items-center justify-center rounded-2xl bg-accent-soft text-accent ring-1 ring-accent/20">
          <Icon name="layers" className="h-6 w-6" />
        </span>
        <h1 className="text-4xl font-semibold leading-[1.05] sm:text-6xl">
          Do your funds
          <span className="block text-muted/80">really diversify?</span>
        </h1>
        <p className="mx-auto mt-5 max-w-md text-muted">Pick two or more funds to see what they hold in common.</p>
      </header>
      <OverlapTool initialCodes={codes} />
    </div>
  );
}
