import Link from "next/link";
import { Icon, type IconName } from "@/components/Icons";
import { DevBanner } from "@/components/Notices";
import { SearchBox } from "@/components/SearchBox";
import { apiGet } from "@/lib/api";
import { formatChange, formatDate, formatValue } from "@/lib/format";
import type { Home } from "@/lib/types";

export const revalidate = 300;

const FEATURES: { icon: IconName; title: string; text: string; href: string }[] = [
  { icon: "chart", title: "Stock research", text: "Fundamentals, red flags, filings and valuation in one page.", href: "#main" },
  { icon: "layers", title: "Fund overlap", text: "See what your funds hold in common.", href: "/tools/overlap" },
  { icon: "sparkles", title: "Ask anything", text: "Research questions answered with sources.", href: "/ask" },
];

export default async function HomePage() {
  const home = await apiGet<Home>("/api/home");
  const data = home.ok ? home.data : null;
  return (
    <div className="space-y-16">
      <section className="flex flex-col items-center pt-10 text-center sm:pt-16">
        <DevBanner show={!!data?.dev_only} />
        <h1 className="max-w-3xl text-4xl font-semibold tracking-tight sm:text-6xl">
          Research Indian markets, <span className="bg-gradient-to-r from-[#8b9bff] to-[#4f5ff0] bg-clip-text text-transparent">clearly.</span>
        </h1>
        <p className="mt-4 max-w-lg text-lg text-muted">Every number with its source. No tips, no noise.</p>
        <div className="mt-8 w-full max-w-2xl">
          <SearchBox />
        </div>
      </section>

      {data && (
        <section aria-labelledby="market-title" className="space-y-4">
          <div className="flex items-baseline justify-between">
            <h2 id="market-title" className="text-lg font-semibold">Markets</h2>
            {data.indices[0] && <span className="text-xs text-muted">Close · {formatDate(data.indices[0].date)}</span>}
          </div>
          <ul className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
            {data.indices.map((i) => {
              const up = (i.change ?? 0) >= 0;
              return (
                <li key={i.name} className="card p-4 transition-colors hover:border-accent/40">
                  <p className="truncate text-xs font-medium text-muted">{i.name}</p>
                  <p className="mt-2 font-mono text-xl font-semibold tabular-nums tracking-tight">{formatValue(i.close, null)}</p>
                  {i.change !== null && (
                    <p className={`mt-2 inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-mono text-xs tabular-nums ${up ? "bg-up/10 text-up" : "bg-down/10 text-down"}`}>
                      <Icon name={up ? "up" : "down"} className="h-3 w-3" />
                      {formatChange(i.change)}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
          {data.breadth && <Breadth b={data.breadth} />}
        </section>
      )}

      <section className="grid gap-4 sm:grid-cols-3">
        {FEATURES.map((f) => (
          <Link key={f.title} href={f.href} className="card group p-6 transition-colors hover:border-accent/40">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent-soft text-accent">
              <Icon name={f.icon} className="h-5 w-5" />
            </span>
            <h3 className="mt-4 font-semibold">{f.title}</h3>
            <p className="mt-1 text-sm text-muted">{f.text}</p>
            <span className="mt-4 inline-flex items-center gap-1 text-sm text-accent opacity-80 transition-opacity group-hover:opacity-100">
              Open <Icon name="arrow" />
            </span>
          </Link>
        ))}
      </section>
    </div>
  );
}

function Breadth({ b }: { b: NonNullable<Home["breadth"]> }) {
  const total = b.advances + b.declines + b.unchanged || 1;
  const pct = (n: number) => `${(100 * n) / total}%`;
  return (
    <div className="card p-4" title={b.formula}>
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium text-up">{b.advances.toLocaleString("en-IN")} advancing</span>
        <span className="text-muted">NSE breadth</span>
        <span className="font-medium text-down">{b.declines.toLocaleString("en-IN")} declining</span>
      </div>
      <div className="mt-3 flex h-2 gap-0.5 overflow-hidden rounded-full" aria-hidden>
        <div className="rounded-l-full bg-up" style={{ width: pct(b.advances) }} />
        <div className="bg-muted/30" style={{ width: pct(b.unchanged) }} />
        <div className="rounded-r-full bg-down" style={{ width: pct(b.declines) }} />
      </div>
    </div>
  );
}
