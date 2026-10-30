"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useState } from "react";
import { Icon } from "@/components/Icons";
import type { SearchResult } from "@/lib/types";

export type PickedFund = { amfi_code: string; name: string };
type Option = { key: string; label: string; detail: string; href: string | null; fund?: PickedFund };

function options(r: SearchResult | null, pickFunds: boolean): Option[] {
  if (!r) return [];
  const funds = r.funds.map((f) => ({ key: `f-${f.amfi_code}`, label: f.name, detail: `Mutual fund · ${f.amfi_code}`, href: `/funds/${f.amfi_code}`, fund: f }));
  if (pickFunds) return funds;
  return [...r.stocks.map((s) => ({ key: `s-${s.isin}`, label: s.symbol, detail: s.name ?? s.isin, href: `/stocks/${encodeURIComponent(s.symbol)}` })), ...funds];
}

/** Site search. With onPickFund it lists funds only and hands the chosen one back instead of navigating. */
export function SearchBox({ onPickFund, placeholder }: { onPickFund?: (f: PickedFund) => void; placeholder?: string } = {}) {
  const router = useRouter();
  const listId = useId();
  const [q, setQ] = useState("");
  const [result, setResult] = useState<SearchResult | null>(null);
  const [active, setActive] = useState(-1);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const term = q.trim();
    if (!term) {
      setResult(null);
      return;
    }
    const ctl = new AbortController();
    const t = setTimeout(() => {
      fetch(`/api/search?q=${encodeURIComponent(term)}`, { signal: ctl.signal })
        .then((r) => (r.ok ? r.json() : null))
        .then((r: SearchResult | null) => {
          setResult(r);
          setActive(-1);
        })
        .catch(() => {});
    }, 200);
    return () => {
      clearTimeout(t);
      ctl.abort();
    };
  }, [q]);

  const opts = options(result, !!onPickFund);
  const go = (o: Option | undefined) => {
    if (!o) return;
    if (onPickFund && o.fund) {
      onPickFund(o.fund);
      setQ("");
      setResult(null);
    } else if (o.href) router.push(o.href);
  };

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setActive((a) => Math.min(a + 1, opts.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      go(opts[active >= 0 ? active : opts.findIndex((o) => o.href)]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  const showList = open && q.trim() !== "" && result !== null;
  return (
    <div className="relative w-full">
      <label htmlFor={`${listId}-input`} className="sr-only">
        Search companies and funds
      </label>
      <input
        id={`${listId}-input`}
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={active >= 0 && opts[active] ? `${listId}-${active}` : undefined}
        autoComplete="off"
        placeholder={placeholder ?? "Search a company or fund"}
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={onKey}
        className="w-full rounded-full border border-white/10 bg-white/[0.04] py-4 pl-13 pr-5 text-base shadow-[0_20px_60px_-30px_rgba(0,0,0,0.8)] outline-none backdrop-blur transition placeholder:text-muted/80 focus:border-accent/60 focus:bg-white/[0.06] focus:ring-4 focus:ring-accent/10"
      />
      <span className="pointer-events-none absolute left-5 top-1/2 -translate-y-1/2 text-muted">
        <Icon name="search" className="h-5 w-5" />
      </span>
      {showList && (
        <ul id={listId} role="listbox" className="absolute top-full z-20 mt-2 max-h-96 w-full overflow-y-auto rounded-3xl border border-white/10 bg-[#0d100c]/95 p-2 text-left shadow-[0_30px_80px_-20px_rgba(0,0,0,0.9)] backdrop-blur-xl">
          {opts.length === 0 && <li className="px-3 py-3 text-sm text-muted">No match among covered companies and funds.</li>}
          {opts.map((o, i) => (
            <li
              key={o.key}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              aria-disabled={!o.href}
              onMouseDown={(e) => {
                e.preventDefault();
                go(o);
              }}
              onMouseEnter={() => setActive(i)}
              className={`flex cursor-pointer items-center gap-3 rounded-2xl px-3 py-2.5 text-sm ${i === active ? "bg-white/[0.06]" : ""}`}
            >
              <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${o.fund ? "bg-white/[0.06] text-foreground" : "bg-accent-soft text-accent"}`}>
                <Icon name={o.fund ? "layers" : "chart"} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{o.label}</span>
                <span className="block truncate text-xs text-muted">{o.detail}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
