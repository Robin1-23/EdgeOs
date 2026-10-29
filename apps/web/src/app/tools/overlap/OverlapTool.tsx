"use client";

import { useEffect, useState } from "react";
import { AgentSection } from "@/components/Evidence";
import { DevBanner } from "@/components/Notices";
import { type PickedFund, SearchBox } from "@/components/SearchBox";
import { anchorId, formatValue } from "@/lib/format";
import { overlapCell } from "@/lib/funds";
import type { Envelope, SearchResult } from "@/lib/types";

type Row = PickedFund & { amount: string };
type Result = { dev_only: boolean; envelope: Envelope };

const MAX_FUNDS = 20;

export function OverlapTool({ initialCodes }: { initialCodes: string[] }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // funds named in the link (?f=code,code): look up their names; amounts are never in the link
  useEffect(() => {
    Promise.all(
      initialCodes.map((c) =>
        fetch(`/api/search?q=${c}`)
          .then((r) => (r.ok ? r.json() : null))
          .then((r: SearchResult | null) => r?.funds.find((f) => f.amfi_code === c) ?? { amfi_code: c, name: `AMFI ${c}` }),
      ),
    ).then((found) => setRows(found.map((f) => ({ ...f, amount: "" }))));
  }, [initialCodes]);

  function update(next: Row[]) {
    setRows(next);
    setResult(null);
    const codes = next.map((r) => r.amfi_code).join(",");
    // the browser's own history: a Next navigation would re-render the page and drop the amounts typed in
    window.history.replaceState(null, "", codes ? `/tools/overlap?f=${codes}` : "/tools/overlap");
  }

  async function compare() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/overlap", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ funds: rows.map((r) => ({ amfi_code: r.amfi_code, amount: Number(r.amount) > 0 ? Number(r.amount) : null })) }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : "the comparison could not be run");
      setResult(body as Result);
    } catch (e) {
      setResult(null);
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <section aria-labelledby="pick-title" className="space-y-3">
        <h2 id="pick-title" className="sr-only">Funds to compare</h2>
        {rows.length < MAX_FUNDS && (
          <SearchBox
            placeholder="Add a fund by name or AMFI code"
            onPickFund={(f) => !rows.some((r) => r.amfi_code === f.amfi_code) && update([...rows, { ...f, amount: "" }])}
          />
        )}
        {rows.length > 0 && (
          <ul className="divide-y divide-line rounded-lg border border-line">
            {rows.map((r, i) => (
              <li key={r.amfi_code} className="flex flex-wrap items-center gap-3 px-3 py-2">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{r.name}</span>
                  <span className="text-xs text-muted">AMFI {r.amfi_code}</span>
                </span>
                <label className="flex items-center gap-1 text-sm">
                  <span className="text-muted">₹</span>
                  <span className="sr-only">Amount invested in {r.name} (optional)</span>
                  <input
                    type="number"
                    inputMode="numeric"
                    min={1}
                    placeholder="amount (optional)"
                    value={r.amount}
                    onChange={(e) => {
                      setRows(rows.map((x, j) => (j === i ? { ...x, amount: e.target.value } : x)));
                      setResult(null);
                    }}
                    className="w-40 rounded border border-line bg-background px-2 py-1 font-mono"
                  />
                </label>
                <button type="button" onClick={() => update(rows.filter((_, j) => j !== i))} className="text-sm text-muted hover:text-foreground" aria-label={`Remove ${r.name}`}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
        <button
          type="button"
          disabled={rows.length < 2 || busy}
          onClick={compare}
          className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-background disabled:opacity-40"
        >
          {busy ? "Comparing…" : rows.length < 2 ? "Pick at least two funds" : `Compare ${rows.length} funds`}
        </button>
        {error && <p role="alert" className="text-sm text-alert">Could not compare: {error}</p>}
      </section>

      {result && (
        <section aria-labelledby="result-title" className="space-y-6">
          <DevBanner show={result.dev_only} />
          <h2 id="result-title" className="text-lg font-semibold">Overlap</h2>
          <OverlapGrid funds={rows} env={result.envelope} />
          <h2 className="text-lg font-semibold">Everything the comparison found</h2>
          <AgentSection envelope={result.envelope} />
        </section>
      )}
    </div>
  );
}

/** Pairwise overlap from MF1: each cell is the shared share of the two portfolios and the stocks both hold. */
function OverlapGrid({ funds, env }: { funds: Row[]; env: Envelope }) {
  return (
    <div className="overflow-x-auto">
      <table className="text-sm">
        <thead>
          <tr>
            <td />
            {funds.map((f) => (
              <th key={f.amfi_code} scope="col" className="max-w-[10rem] truncate px-2 pb-2 text-left text-xs font-normal text-muted" title={f.name}>
                {f.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {funds.map((a) => (
            <tr key={a.amfi_code} className="border-t border-line">
              <th scope="row" className="max-w-[12rem] truncate py-2 pr-3 text-left text-xs font-normal text-muted" title={a.name}>
                {a.name}
              </th>
              {funds.map((b) => {
                if (a.amfi_code === b.amfi_code) return <td key={b.amfi_code} className="px-2 text-center text-muted">—</td>;
                const c = overlapCell(env, a.amfi_code, b.amfi_code);
                return (
                  <td key={b.amfi_code} className="px-2 py-2 font-mono tabular-nums">
                    {c.overlap ? (
                      <a href={`#${anchorId(`MF1:${c.overlap.id}`)}`} className="hover:text-accent">
                        {formatValue(c.overlap.value, c.overlap.unit)}
                        {c.common && <span className="block text-[11px] text-muted">{formatValue(c.common.value, null)} common</span>}
                      </a>
                    ) : (
                      <span className="text-muted" title="No current portfolio for one of the two funds">n/a</span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
