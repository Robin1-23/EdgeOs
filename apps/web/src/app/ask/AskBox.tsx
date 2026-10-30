"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/components/Auth";
import { AGENT_TITLES, AgentSection, CitedList, citeLabels } from "@/components/Evidence";
import { Icon } from "@/components/Icons";
import { DevBanner } from "@/components/Notices";
import { anchorId } from "@/lib/format";
import type { AgentSlot, Envelope, Report } from "@/lib/types";

type Evidence = AgentSlot & { agent_id: string; subject: string; prefix: string };
type Answered = { status: "answered"; dev_only: boolean; answer: Report; evidence: Evidence[]; remaining: number };
type Other = { status: "needs_clarification" | "unsupported" | "blocked" | "failed"; message: string; remaining: number };
type Result = Answered | Other;

const MAX = 500;
const EXAMPLES = [
  "How have Voltas' revenue and margins changed over five years?",
  "Is HDFC Bank's valuation high compared with its own history?",
  "How much do Parag Parikh Flexi Cap and HDFC Flexi Cap overlap?",
];
// The API answers in one response; these are timed messages, not live progress.
const STAGES: [number, string][] = [
  [0, "Working out what to look up…"],
  [4, "Running the specialist agents…"],
  [12, "Checking every number against its source…"],
  [20, "Writing the answer and checking it for compliance…"],
];

const ERRORS: Record<number, string> = {
  401: "Your sign-in has expired. Sign in again to ask.",
  404: "The answer uses data that is not licensed for display yet, so it cannot be shown.",
  503: "The ask box is not switched on yet.",
};

export function AskBox() {
  const { configured, ready, user, openSignIn } = useAuth();
  const [question, setQuestion] = useState("");
  const [quota, setQuota] = useState<{ limit: number; remaining: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const reopenId = useSearchParams().get("id");

  useEffect(() => {
    if (!user) return setQuota(null);
    user
      .getIdToken()
      .then((t) => fetch("/api/ask/quota", { headers: { Authorization: `Bearer ${t}` } }))
      .then((r) => (r.ok ? r.json() : null))
      .then(setQuota)
      .catch(() => setQuota(null));
  }, [user]);

  // /ask?id=N re-opens one of the user's own past answers (from the account page)
  useEffect(() => {
    if (!user || !reopenId) return;
    user
      .getIdToken()
      .then((t) => fetch(`/api/ask/${encodeURIComponent(reopenId)}`, { headers: { Authorization: `Bearer ${t}` } }))
      .then(async (r) => {
        const body = await r.json();
        if (!r.ok) throw new Error(body.detail ?? "that question could not be opened");
        setQuestion(body.question);
        setResult({ ...body, remaining: 0 } as Result);
      })
      .catch((e: Error) => setError(e.message));
  }, [user, reopenId]);

  useEffect(() => {
    if (!busy) return;
    const started = Date.now();
    const t = setInterval(() => setElapsed((Date.now() - started) / 1000), 500);
    return () => clearInterval(t);
  }, [busy]);

  async function submit(ev: React.FormEvent) {
    ev.preventDefault();
    if (!user) return openSignIn();
    const ctl = new AbortController();
    abort.current = ctl;
    setBusy(true);
    setElapsed(0);
    setError(null);
    setResult(null);
    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        signal: ctl.signal,
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${await user.getIdToken()}` },
        body: JSON.stringify({ question: question.trim() }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (res.status === 429) setQuota((q) => (q ? { ...q, remaining: 0 } : q));
        throw new Error(res.status === 429 ? body.detail : ERRORS[res.status] ?? `The research service failed (${res.status}).`);
      }
      setResult(body as Result);
      setQuota((q) => (q ? { ...q, remaining: body.remaining } : q));
    } catch (e) {
      if ((e as Error).name !== "AbortError") setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const stage = [...STAGES].reverse().find(([s]) => elapsed >= s)?.[1];
  const canAsk = question.trim().length >= 3 && !busy && quota?.remaining !== 0;

  return (
    <div className="space-y-8">
      <form onSubmit={submit} className="mx-auto w-full max-w-3xl space-y-4">
        <div className="card p-2 shadow-sm transition focus-within:border-accent focus-within:ring-4 focus-within:ring-accent/15">
          <label htmlFor="question" className="sr-only">Your question</label>
          <textarea
            id="question"
            rows={3}
            maxLength={MAX}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && canAsk && user) {
                e.preventDefault();
                e.currentTarget.form?.requestSubmit();
              }
            }}
            placeholder="Ask about a company or a fund…"
            className="w-full resize-none bg-transparent px-3 py-2 text-base outline-none placeholder:text-muted/70"
          />
          <div className="flex items-center justify-between gap-3 px-2 pb-1">
            <span className="text-xs text-muted">
              {quota ? `${quota.remaining} of ${quota.limit} left today` : `${question.length}/${MAX}`}
            </span>
            {!configured ? (
              <span className="text-xs text-muted">Not switched on yet</span>
            ) : !ready ? null : !user ? (
              <button type="button" onClick={openSignIn} className="rounded-full bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90">
                Sign in to ask
              </button>
            ) : busy ? (
              <button type="button" onClick={() => abort.current?.abort()} className="rounded-full border border-line px-4 py-2 text-sm hover:bg-surface-2">
                Stop
              </button>
            ) : (
              <button type="submit" disabled={!canAsk} aria-label="Ask"
                      className="flex h-10 w-10 items-center justify-center rounded-full bg-accent text-white transition-opacity hover:opacity-90 disabled:opacity-30">
                <Icon name="send" />
              </button>
            )}
          </div>
        </div>
        {!question && !result && (
          <ul className="flex flex-wrap justify-center gap-2">
            {EXAMPLES.map((q) => (
              <li key={q}>
                <button type="button" onClick={() => setQuestion(q)} className="rounded-full border border-line bg-surface px-3.5 py-1.5 text-sm text-muted transition-colors hover:border-accent/40 hover:text-foreground">
                  {q}
                </button>
              </li>
            ))}
          </ul>
        )}
        {quota?.remaining === 0 && <p className="text-center text-xs text-muted">Your allowance resets at midnight IST.</p>}
      </form>

      {busy && (
        <p role="status" className="mx-auto flex max-w-3xl items-center gap-3 text-sm text-muted">
          <span className="h-2 w-2 animate-pulse rounded-full bg-accent" />
          {stage} <span className="font-mono text-xs">{Math.floor(elapsed)}s</span>
        </p>
      )}
      {error && <p role="alert" className="mx-auto max-w-3xl text-sm text-alert">{error}</p>}
      {result && (result.status === "answered" ? <Answer r={result} /> : <Message r={result} />)}
    </div>
  );
}

function Message({ r }: { r: Other }) {
  const title = { needs_clarification: "Could you be more specific?", unsupported: "Outside what EdgeOS covers", blocked: "No answer could be shown", failed: "Something went wrong" }[r.status];
  return (
    <section role="status" className="card mx-auto max-w-3xl p-5">
      <h2 className="font-semibold">{title}</h2>
      <p className="mt-1 text-sm text-muted">{r.message}</p>
    </section>
  );
}

function Answer({ r }: { r: Answered }) {
  const available = r.evidence.filter((e): e is Evidence & { available: true; envelope: Envelope } => e.available);
  const prefixes = new Map(available.map((e) => [e.envelope, e.prefix]));
  const labels = citeLabels(available.map((e) => e.envelope), (env) => prefixes.get(env) ?? env.agent_id);
  return (
    <article className="mx-auto max-w-3xl space-y-6">
      <DevBanner show={r.dev_only} />
      <section aria-labelledby="answer-title" className="space-y-3">
        <h2 id="answer-title" className="text-lg font-semibold">Answer</h2>
        <p className="text-xs text-muted">Confidence {r.answer.confidence}: {r.answer.confidence_reason}</p>
        <CitedList items={r.answer.summary} labels={labels} />
        {r.answer.key_items.length > 0 && (
          <ul className="grid gap-2 pt-2 sm:grid-cols-2">
            {r.answer.key_items.map((k) => (
              <li key={k.id} className="rounded border border-line px-3 py-2 text-sm">
                <a href={`#${anchorId(k.id)}`} className="hover:text-accent">{k.text}</a>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-labelledby="ask-evidence-title" className="space-y-4">
        <h2 id="ask-evidence-title" className="text-lg font-semibold">The evidence</h2>
        {r.evidence.map((e) => (
          <details key={e.prefix} open className="rounded-lg border border-line">
            <summary className="cursor-pointer px-4 py-3 font-medium">{AGENT_TITLES[e.agent_id] ?? e.agent_id} · {e.subject}</summary>
            <div className="border-t border-line px-4 py-3">
              {e.available ? <AgentSection envelope={e.envelope} prefix={e.prefix} /> : <p className="text-sm text-muted">Not available: {e.reason}.</p>}
            </div>
          </details>
        ))}
      </section>
      {r.answer.gaps.length > 0 && (
        <section className="text-xs text-muted">
          <h2 className="font-medium">What this answer could not cover</h2>
          <ul className="mt-1 space-y-0.5">{r.answer.gaps.map((g, i) => <li key={i}>{g}</li>)}</ul>
        </section>
      )}
      <p className="text-xs text-muted">{r.answer.disclaimer}</p>
    </article>
  );
}
