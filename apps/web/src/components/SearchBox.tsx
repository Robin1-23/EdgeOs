"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useState } from "react";
import type { SearchResult } from "@/lib/types";

type Option = { key: string; label: string; detail: string; href: string | null };

function options(r: SearchResult | null): Option[] {
  if (!r) return [];
  return [
    ...r.stocks.map((s) => ({ key: `s-${s.isin}`, label: s.symbol, detail: s.name ?? s.isin, href: `/stocks/${encodeURIComponent(s.symbol)}` })),
    // fund pages arrive in a later step; listed so a search for a fund shows it exists
    ...r.funds.map((f) => ({ key: `f-${f.amfi_code}`, label: f.name, detail: `Fund · AMFI ${f.amfi_code} · page coming soon`, href: null })),
  ];
}

export function SearchBox() {
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

  const opts = options(result);
  const go = (o: Option | undefined) => o?.href && router.push(o.href);

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
    <div className="relative w-full max-w-2xl">
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
        placeholder="Search by company, NSE symbol, ISIN or fund name"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={onKey}
        className="w-full rounded-lg border border-line bg-background px-4 py-3 text-base outline-none focus:border-accent focus:ring-2 focus:ring-accent/30"
      />
      {showList && (
        <ul id={listId} role="listbox" className="absolute z-10 mt-1 w-full overflow-hidden rounded-lg border border-line bg-background shadow-lg">
          {opts.length === 0 && <li className="px-4 py-3 text-sm text-muted">No covered company or fund matches.</li>}
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
              className={`flex items-baseline justify-between gap-4 px-4 py-2 text-sm ${o.href ? "cursor-pointer" : "text-muted"} ${i === active ? "bg-panel" : ""}`}
            >
              <span className="font-medium truncate">{o.label}</span>
              <span className="text-xs text-muted truncate">{o.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
