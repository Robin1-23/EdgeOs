import Link from "next/link";
import { Icon } from "@/components/Icons";

// Same text the compliance guard appends to every report (services/agents/mother/compliance.DISCLAIMER).
export const DISCLAIMER =
  "This is an automated, descriptive analysis based on the sources cited. It is not investment advice, a research " +
  "report or a recommendation to transact in any security or fund. EdgeOS is not registered with SEBI as an " +
  "investment adviser or research analyst.";

export function DevBanner({ show }: { show: boolean }) {
  if (!show) return null;
  return (
    <p
      role="note"
      title="These figures come from files downloaded for development; their terms do not allow publishing them."
      className="mb-6 inline-flex items-center gap-2 rounded-full border border-watch/30 bg-watch/10 px-3 py-1 text-xs font-medium text-watch"
    >
      <span className="h-1.5 w-1.5 rounded-full bg-watch" />
      Development data · not for public display
    </p>
  );
}

/** A clear page for "not covered", "no report yet" and "not licensed", instead of a generic error. */
export function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="mx-auto max-w-md py-24 text-center">
      <div className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-2xl bg-accent-soft text-accent">
        <Icon name="search" className="h-5 w-5" />
      </div>
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      <p className="mt-2 text-muted">{detail}</p>
      <Link href="/" className="mt-8 inline-flex items-center gap-2 rounded-full bg-accent px-5 py-2.5 text-sm font-medium text-accent-ink hover:opacity-90">
        Search again <Icon name="arrow" />
      </Link>
    </div>
  );
}
