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

/** Short labels for citation chips: "M1:close" -> "Close". Unknown ids fall back to the agent title. */
export function citeLabels(envelopes: Envelope[]): Map<string, string> {
  const out = new Map<string, string>();
  for (const e of envelopes) {
    for (const m of e.metrics) out.set(`${e.agent_id}:${m.id}`, m.name);
    for (const f of e.findings) out.set(`${e.agent_id}:finding:${f.id}`, `${AGENT_TITLES[e.agent_id] ?? e.agent_id}: finding`);
    e.flags.forEach((_, i) => out.set(`${e.agent_id}:flag:${i}`, `${AGENT_TITLES[e.agent_id] ?? e.agent_id}: flag`));
  }
  return out;
}

export function CitedList({ items, labels, empty }: { items: CitedText[]; labels: Map<string, string>; empty?: string }) {
  if (!items.length) return empty ? <p className="text-sm text-muted">{empty}</p> : null;
  return (
    <ul className="space-y-2">
      {items.map((p, i) => (
        <li key={i} className="leading-relaxed">
          {p.text}{" "}
          {p.cites.map((c) => (
            <a
              key={c}
              href={`#${anchorId(c)}`}
              title={labels.get(c) ?? c}
              className="ml-1 inline-block max-w-[14rem] truncate rounded bg-panel px-1.5 py-0.5 align-middle text-[11px] text-muted hover:text-accent"
            >
              {labels.get(c) ?? c}
            </a>
          ))}
        </li>
      ))}
    </ul>
  );
}

const SEVERITY = { info: "border-line text-muted", watch: "border-watch/60 text-watch", alert: "border-alert/60 text-alert" };

/** One agent's result: metrics (each with its formula and source), findings, flags and what it could not compute. */
export function AgentSection({ envelope: e }: { envelope: Envelope }) {
  const sources = new Map(e.sources.map((s) => [s.id, s]));
  return (
    <div className="space-y-4">
      <p className="text-xs text-muted">
        As of {formatDate(e.as_of)} · confidence {e.confidence.level}: {e.confidence.reason}
      </p>
      {e.flags.length > 0 && (
        <ul className="space-y-1">
          {e.flags.map((f, i) => (
            <li key={i} id={anchorId(`${e.agent_id}:flag:${i}`)} className={`rounded border px-3 py-1.5 text-sm ${SEVERITY[f.severity]}`}>
              <span className="mr-2 text-[11px] font-semibold uppercase tracking-wide">{f.severity}</span>
              <span className="text-foreground">{f.reason}</span>
            </li>
          ))}
        </ul>
      )}
      {e.findings.length > 0 && (
        <ul className="list-disc space-y-1 pl-5 text-sm">
          {e.findings.map((f) => (
            <li key={f.id} id={anchorId(`${e.agent_id}:finding:${f.id}`)}>
              {f.text}
            </li>
          ))}
        </ul>
      )}
      {e.metrics.length > 0 && (
        <table className="w-full text-sm">
          <thead className="sr-only">
            <tr>
              <th>Measure</th>
              <th>Value</th>
              <th>Formula and source</th>
            </tr>
          </thead>
          <tbody>
            {e.metrics.map((m) => {
              const src = sources.get(m.source_query_id);
              return (
                <tr key={m.id} id={anchorId(`${e.agent_id}:${m.id}`)} className="border-b border-line align-top last:border-0">
                  <td className="py-1.5 pr-3">{m.name}</td>
                  <td className="py-1.5 pr-3 text-right font-mono tabular-nums whitespace-nowrap">{formatValue(m.value, m.unit)}</td>
                  <td className="w-8 py-1.5 text-right">
                    <details className="group relative">
                      <summary aria-label={`How "${m.name}" is computed`} className="cursor-pointer list-none text-muted hover:text-accent">
                        ⓘ
                      </summary>
                      <div className="absolute right-0 z-10 mt-1 w-72 rounded border border-line bg-background p-3 text-left text-xs shadow-lg">
                        <p>{m.formula ?? "Formula definition missing."}</p>
                        {src && <p className="mt-2 text-muted break-words">Source: {src.ref}</p>}
                      </div>
                    </details>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {e.gaps.length > 0 && (
        <div className="text-xs text-muted">
          <p className="font-medium">Not available</p>
          <ul className="mt-1 space-y-0.5">
            {e.gaps.map((g, i) => (
              <li key={i}>
                {g.item}: {g.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
