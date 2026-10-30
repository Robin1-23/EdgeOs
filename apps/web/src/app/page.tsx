import Link from "next/link";
import { Icon, type IconName } from "@/components/Icons";
import { MarketPreview } from "@/components/MarketPreview";
import { DevBanner } from "@/components/Notices";
import { SearchBox } from "@/components/SearchBox";
import { apiGet } from "@/lib/api";
import type { Home } from "@/lib/types";

export const revalidate = 300;

const POPULAR = ["RELIANCE", "HDFCBANK", "TCS", "INFY", "VOLTAS"];

const STATS = [
  { value: "10", label: "specialist agents" },
  { value: "2,000+", label: "NSE stocks covered" },
  { value: "100%", label: "numbers with a source" },
  { value: "0", label: "buy or sell tips" },
];

const FEATURES: { icon: IconName; title: string; text: string; href: string; cta: string }[] = [
  { icon: "chart", title: "Stock research", text: "Fundamentals, red flags, filings, peers and valuation ranges, on one page.", href: "#search", cta: "Search a stock" },
  { icon: "layers", title: "Fund overlap", text: "See how much your mutual funds really hold in common, and what the regular plan costs you.", href: "/tools/overlap", cta: "Compare funds" },
  { icon: "sparkles", title: "Ask EdgeOS", text: "Ask a research question in plain English. Every sentence in the answer shows its source.", href: "/ask", cta: "Ask a question" },
];

export default async function HomePage() {
  const home = await apiGet<Home>("/api/home");
  const data = home.ok ? home.data : null;
  return (
    <div className="space-y-24">
      <section className="flex flex-col items-center pt-8 text-center sm:pt-14">
        {data?.dev_only ? (
          <DevBanner show />
        ) : (
          <p className="mb-6 inline-flex items-center gap-2 rounded-full border border-line bg-white/[0.03] px-3.5 py-1.5 text-xs text-muted">
            <span className="h-1.5 w-1.5 rounded-full bg-accent shadow-[0_0_8px_var(--accent)]" />
            Research for NSE stocks and Indian mutual funds
          </p>
        )}
        <h1 className="rise max-w-4xl text-5xl font-semibold leading-[1.02] sm:text-7xl lg:text-[5.5rem]">
          Research without
          <span className="block text-muted/80">the noise.</span>
        </h1>
        <p className="rise mt-6 max-w-xl text-base text-muted sm:text-lg" style={{ "--d": "120ms" } as React.CSSProperties}>
          Specialist agents read the filings, prices and fund portfolios. You get the facts, each with its source.
        </p>
        <div id="search" className="rise mt-10 w-full max-w-2xl" style={{ "--d": "220ms" } as React.CSSProperties}>
          <SearchBox />
        </div>
        <ul className="mt-4 flex flex-wrap items-center justify-center gap-2 text-xs">
          <li className="text-muted">Popular:</li>
          {POPULAR.map((s) => (
            <li key={s}>
              <Link href={`/stocks/${s}`} className="rounded-full border border-line px-3 py-1.5 font-mono text-muted transition-colors hover:border-accent/50 hover:text-foreground">
                {s}
              </Link>
            </li>
          ))}
        </ul>
      </section>

      {data && data.indices.length > 0 && (
        <div className="rise" style={{ "--d": "340ms" } as React.CSSProperties}>
          <MarketPreview home={data} />
        </div>
      )}

      <section aria-label="EdgeOS in numbers" className="grid grid-cols-2 gap-px overflow-hidden rounded-[1.25rem] border border-line bg-line md:grid-cols-4">
        {STATS.map((s) => (
          <div key={s.label} className="bg-background px-6 py-8">
            <p className="font-display text-4xl font-semibold tracking-tight">{s.value}</p>
            <p className="mt-1 text-sm text-muted">{s.label}</p>
          </div>
        ))}
      </section>

      <section aria-labelledby="features-title" className="space-y-10">
        <div className="max-w-2xl">
          <p className="text-xs font-medium uppercase tracking-[0.16em] text-accent">What you get</p>
          <h2 id="features-title" className="mt-3 text-3xl font-semibold sm:text-5xl">
            Three tools. <span className="text-muted/80">One rule: show the source.</span>
          </h2>
        </div>
        <div className="grid gap-4 md:grid-cols-3">
          {FEATURES.map((f) => (
            <Link key={f.title} href={f.href} className="card card-hover group flex flex-col p-7">
              <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-accent-soft text-accent ring-1 ring-accent/20">
                <Icon name={f.icon} className="h-5 w-5" />
              </span>
              <h3 className="mt-6 text-xl font-semibold">{f.title}</h3>
              <p className="mt-2 flex-1 text-sm leading-relaxed text-muted">{f.text}</p>
              <span className="mt-6 inline-flex items-center gap-1.5 text-sm font-medium text-foreground">
                {f.cta}
                <Icon name="arrow" className="h-4 w-4 text-accent transition-transform group-hover:translate-x-0.5" />
              </span>
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}
