import type { Metadata } from "next";
import { AskBox } from "./AskBox";

export const metadata: Metadata = {
  title: "Ask",
  description: "Ask a research question about an NSE-listed company or an Indian mutual fund; answers cite their evidence.",
};

export default function AskPage() {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Ask a research question</h1>
        <p className="mt-1 max-w-2xl text-muted">
          About one or a few NSE-listed companies or mutual funds. The answer describes what the evidence shows, and
          every sentence links to the number, formula and source behind it. It never recommends what to buy or sell.
        </p>
      </header>
      <AskBox />
    </div>
  );
}
