import Link from "next/link";

// Same text the compliance guard appends to every report (services/agents/mother/compliance.DISCLAIMER).
export const DISCLAIMER =
  "This is an automated, descriptive analysis based on the sources cited. It is not investment advice, a research " +
  "report or a recommendation to transact in any security or fund. EdgeOS is not registered with SEBI as an " +
  "investment adviser or research analyst.";

export function DevBanner({ show }: { show: boolean }) {
  if (!show) return null;
  return (
    <div role="note" className="mb-4 rounded border border-watch/50 bg-watch/10 px-3 py-2 text-sm">
      <strong>Development data, not for public display.</strong> These figures come from files downloaded for
      development; their terms do not allow publishing them.
    </div>
  );
}

/** A clear page for "not covered", "no report yet" and "not licensed", instead of a generic error. */
export function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="mx-auto max-w-xl py-16 text-center">
      <h1 className="text-xl font-semibold">{title}</h1>
      <p className="mt-2 text-muted">{detail}</p>
      <Link href="/" className="mt-6 inline-block text-accent underline underline-offset-4">
        Search for another company
      </Link>
    </div>
  );
}
