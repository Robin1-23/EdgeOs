"use client";

import { useEffect, useRef, useState } from "react";
import { AgentSection } from "@/components/Evidence";
import type { AgentSlot } from "@/lib/types";

export const SHORT_TITLES: Record<string, string> = {
  M1: "Technicals", M2: "Fundamentals", M3: "Red flags", M4: "Filings", M5: "News", M6: "Peers", M7: "Valuation",
  MF2: "Returns", MF1: "Portfolio X-ray", MF3: "Closet index",
};

/** One agent at a time. A citation anywhere on the page that points into a hidden tab opens that tab first. */
export function AgentTabs({ ids, agents, extra }: { ids: string[]; agents: Record<string, AgentSlot>; extra?: Record<string, React.ReactNode> }) {
  const [active, setActive] = useState(ids[0]);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function open(hash: string) {
      const target = hash && root.current?.querySelector<HTMLElement>(`#${CSS.escape(hash.slice(1))}`);
      const panel = target ? target.closest<HTMLElement>("[data-agent]") : null;
      if (!target || !panel) return false;
      setActive(panel.dataset.agent!);
      requestAnimationFrame(() => target.scrollIntoView({ behavior: "smooth", block: "center" }));
      return true;
    }
    function onClick(ev: MouseEvent) {
      const a = (ev.target as HTMLElement).closest<HTMLAnchorElement>('a[href^="#"]');
      if (a && open(a.hash)) {
        ev.preventDefault();
        history.replaceState(null, "", a.hash);
      }
    }
    open(location.hash);
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, []);

  return (
    <section aria-labelledby="detail-title" ref={root} className="space-y-4">
      <h2 id="detail-title" className="text-2xl font-semibold">Evidence <span className="text-muted/80">by agent</span></h2>
      <div role="tablist" aria-label="Evidence by agent" className="-mx-5 flex gap-1 overflow-x-auto px-5 pb-1 sm:mx-0 sm:w-fit sm:rounded-full sm:border sm:border-line sm:p-1">
        {ids.map((id) => {
          const slot = agents[id];
          const flagged = slot?.available && slot.envelope.flags.some((f) => f.severity !== "info");
          return (
            <button
              key={id}
              type="button"
              role="tab"
              id={`tab-${id}`}
              aria-selected={active === id}
              aria-controls={`panel-${id}`}
              onClick={() => setActive(id)}
              className={`relative shrink-0 rounded-full px-4 py-2 text-sm transition-colors ${
                active === id ? "bg-accent text-accent-ink font-medium" : "text-muted hover:bg-white/[0.05] hover:text-foreground"
              } ${slot?.available ? "" : "opacity-60"}`}
            >
              {SHORT_TITLES[id] ?? id}
              {flagged && <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-watch" aria-label="has flags" />}
            </button>
          );
        })}
      </div>
      {ids.map((id) => {
        const slot = agents[id];
        return (
          <div key={id} role="tabpanel" id={`panel-${id}`} aria-labelledby={`tab-${id}`} data-agent={id} hidden={active !== id} className="card p-6">
            {!slot ? (
              <p className="text-sm text-muted">Not part of this report.</p>
            ) : slot.available ? (
              <div className="space-y-5">
                {extra?.[id]}
                <AgentSection envelope={slot.envelope} />
              </div>
            ) : (
              <p className="text-sm text-muted">Not available: {slot.reason}.</p>
            )}
          </div>
        );
      })}
    </section>
  );
}
