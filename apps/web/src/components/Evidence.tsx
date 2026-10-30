import { Icon } from "@/components/Icons";
import { anchorId, formatDate, formatValue } from "@/lib/format";
import type { CitedText, Envelope } from "@/lib/types";

export const AGENT_TITLES: Record<string, string> = {
  M1: "Price and technicals",
  M2: "Fundamentals",
  M3: "Forensic red flags",
  M4: "Filings and concalls",
  M5: "News and sentiment",
  M6: "Peers and sector",
  M7: "Valuation ranges",
  MF2: "Returns and consistency",
  MF1: "Portfolio X-ray",
  MF3: "Closet-index check",
};

/** Short labels for citation chips: "M1:close" -> "Close". Citation ids start with the agent id, or with
 * "SUBJECT/AGENT" when an answer covers several subjects: pass `prefixOf` for that. */
export function citeLabels(envelopes: Envelope[], prefixOf: (e: Envelope) => string = (e) => e.agent_id): Map<string, string> {
  const out = new Map<string, string>();
  for (const e of envelopes) {
    const p = prefixOf(e);
    const title = AGENT_TITLES[e.agent_id] ?? e.agent_id;
    for (const m of e.metrics) out.set(`${p}:${m.id}`, p === e.agent_id ? m.name : `${e.subject}: ${m.name}`);
    for (const f of e.findings) out.set(`${p}:finding:${f.id}`, `${title}: finding`);
    e.flags.forEach((_, i) => out.set(`${p}:flag:${i}`, `${title}: flag`));
  }
  return out;
}

/** Sentences with their citations as small numbered markers; each opens the number it cites (full name on hover). */
export function CitedList({ items, labels, empty, tone }: { items: CitedText[]; labels: Map<string, string>; empty?: string; tone?: "up" | "down" }) {
  if (!items.length) return empty ? <p className="text-sm text-muted">{empty}</p> : null;
  const dot = tone === "up" ? "bg-up" : tone === "down" ? "bg-down" : "bg-accent";
  return (
    <ul className="space-y-3">
      {items.map((p, i) => (
        <li key={i} className="flex gap-3 leading-relaxed">
          <span className={`mt-2.5 h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} aria-hidden />
          <span>
            {p.text}
            {p.cites.length > 0 && (
              <a
                href={`#${anchorId(p.cites[0])}`}
                title={`Sources: ${p.cites.map((c) => labels.get(c) ?? c).join(" · ")}`}
                aria-label={`${p.cites.length} source${p.cites.length > 1 ? "s" : ""}: ${p.cites.map((c) => labels.get(c) ?? c).join(", ")}`}
                className="ml-1.5 inline-flex items-center gap-1 rounded-full bg-surface-2 px-1.5 py-0.5 align-[1px] text-[10px] font-medium text-muted no-underline transition-colors hover:bg-accent hover:text-accent-ink"
              >
                <Icon name="info" className="h-3 w-3" />
                {p.cites.length}
              </a>
            )}
          </span>
        </li>
      ))}
    </ul>
  );
}

const SEVERITY = {
  info: "bg-white/[0.06] text-muted",
  watch: "bg-watch/10 text-watch",
  alert: "bg-alert/10 text-alert",
};

/** One agent's result: flags, findings, metric tiles (each with its formula and source) and what it could not compute. */
export function AgentSection({ envelope: e, prefix = e.agent_id }: { envelope: Envelope; prefix?: string }) {
  const sources = new Map(e.sources.map((s) => [s.id, s]));
  return (
    <div className="space-y-5">
      {e.flags.length > 0 && (
        <ul className="flex flex-wrap gap-2">
          {e.flags.map((f, i) => (
            <li key={i} id={anchorId(`${prefix}:flag:${i}`)} className={`inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-sm ${SEVERITY[f.severity]}`}>
              <Icon name="alert" className="h-3.5 w-3.5" />
              <span>{f.reason}</span>
            </li>
          ))}
        </ul>
      )}
      {e.findings.length > 0 && (
        <ul className="space-y-2 text-sm">
          {e.findings.map((f) => (
            <li key={f.id} id={anchorId(`${prefix}:finding:${f.id}`)} className="rounded-2xl border border-line bg-white/[0.02] px-4 py-3">
              {f.text}
            </li>
          ))}
        </ul>
      )}
      {e.metrics.length > 0 && (
        <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
          {e.metrics.map((m) => {
            const src = sources.get(m.source_query_id);
            return (
              <li key={m.id} id={anchorId(`${prefix}:${m.id}`)} className="relative rounded-2xl border border-line bg-white/[0.02] px-4 py-3.5 transition-colors hover:border-white/15">
                <div className="flex items-start justify-between gap-2">
                  <span className="text-xs leading-snug text-muted">{m.name}</span>
                  <details className="relative shrink-0">
                    <summary aria-label={`How "${m.name}" is computed`} className="-m-1 flex h-6 w-6 cursor-pointer list-none items-center justify-center rounded-full text-muted hover:bg-surface hover:text-accent">
                      <Icon name="info" className="h-3.5 w-3.5" />
                    </summary>
                    <div className="absolute right-0 z-20 mt-2 w-72 rounded-2xl border border-white/10 bg-[#0d100c] p-4 text-left text-xs shadow-[0_30px_80px_-20px_rgba(0,0,0,0.9)]">
                      <p className="leading-relaxed">{m.formula ?? "Formula definition missing."}</p>
                      {src && <p className="mt-2 break-words text-muted">Source: {src.ref}</p>}
                    </div>
                  </details>
                </div>
                <p className="mt-2 font-mono text-lg font-medium tabular-nums">{formatValue(m.value, m.unit)}</p>
              </li>
            );
          })}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted">
        <span>As of {formatDate(e.as_of)}</span>
        <span title={e.confidence.reason}>Confidence: {e.confidence.level}</span>
        {e.gaps.length > 0 && (
          <details className="w-full">
            <summary className="cursor-pointer select-none hover:text-foreground">{e.gaps.length} item{e.gaps.length > 1 ? "s" : ""} not available</summary>
            <ul className="mt-2 space-y-1 border-l border-line pl-3">
              {e.gaps.map((g, i) => (
                <li key={i}>
                  <span className="text-foreground/80">{g.item}</span>: {g.reason}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </div>
  );
}
