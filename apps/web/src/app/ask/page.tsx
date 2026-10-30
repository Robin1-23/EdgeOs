import type { Metadata } from "next";
import { Suspense } from "react";
import { Icon } from "@/components/Icons";
import { AskBox } from "./AskBox";

export const metadata: Metadata = {
  title: "Ask",
  description: "Ask a research question about an NSE-listed company or an Indian mutual fund; answers cite their evidence.",
};

export default function AskPage() {
  return (
    <div className="space-y-10 pt-10 sm:pt-16">
      <header className="text-center">
        <span className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-2xl bg-accent-soft text-accent">
          <Icon name="sparkles" className="h-6 w-6" />
        </span>
        <h1 className="text-3xl font-semibold tracking-tight sm:text-5xl">Ask EdgeOS</h1>
        <p className="mx-auto mt-3 max-w-md text-muted">Answers from the evidence, with every source. Never a tip.</p>
      </header>
      <Suspense>
        <AskBox />
      </Suspense>
    </div>
  );
}
