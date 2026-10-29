import { DevBanner } from "@/components/Notices";
import { SearchBox } from "@/components/SearchBox";
import { apiGet } from "@/lib/api";
import { formatChange, formatDate, formatValue } from "@/lib/format";
import type { Home } from "@/lib/types";

export const revalidate = 300;

export default async function HomePage() {
  const home = await apiGet<Home>("/api/home");
  const data = home.ok ? home.data : null;
  return (
    <div className="space-y-10">
      <DevBanner show={!!data?.dev_only} />
      <section className="flex flex-col items-center gap-4 pt-6 text-center">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Research Indian stocks and funds</h1>
        <p className="max-w-xl text-muted">
          What the filings, prices and fund portfolios show, with the formula and source behind every number.
        </p>
        <SearchBox />
      </section>

      <section aria-labelledby="market-title">
        <h2 id="market-title" className="mb-3 text-sm font-semibold text-muted">
          Market overview
        </h2>
        {!data ? (
          <p className="text-sm text-muted">Market data is not available right now ({home.ok ? "" : home.detail}).</p>
        ) : (
          <>
            <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              {data.indices.map((i) => (
                <li key={i.name} className="rounded-lg border border-line p-3">
                  <p className="truncate text-xs text-muted">{i.name}</p>
                  <p className="mt-1 font-mono text-lg tabular-nums">{formatValue(i.close, null)}</p>
                  <p className={`font-mono text-xs tabular-nums ${i.change === null ? "text-muted" : i.change >= 0 ? "text-up" : "text-down"}`}>
                    {formatChange(i.change)} <span className="text-muted">· {formatDate(i.date)}</span>
                  </p>
                </li>
              ))}
            </ul>
            {data.breadth && <Breadth b={data.breadth} />}
          </>
        )}
      </section>
    </div>
  );
}

function Breadth({ b }: { b: NonNullable<Home["breadth"]> }) {
  const total = b.advances + b.declines + b.unchanged || 1;
  return (
    <div className="mt-4 rounded-lg border border-line p-3">
      <p className="text-xs text-muted" title={b.formula}>
        NSE stocks on {formatDate(b.date)}: {b.advances.toLocaleString("en-IN")} rose, {b.declines.toLocaleString("en-IN")} fell,{" "}
        {b.unchanged.toLocaleString("en-IN")} unchanged
      </p>
      <div className="mt-2 flex h-2 overflow-hidden rounded" aria-hidden>
        <div className="bg-up" style={{ width: `${(100 * b.advances) / total}%` }} />
        <div className="bg-muted/40" style={{ width: `${(100 * b.unchanged) / total}%` }} />
        <div className="bg-down" style={{ width: `${(100 * b.declines) / total}%` }} />
      </div>
    </div>
  );
}
