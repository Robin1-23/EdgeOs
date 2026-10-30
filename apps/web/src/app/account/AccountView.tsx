"use client";

import { deleteUser } from "firebase/auth";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/components/Auth";
import { formatDate } from "@/lib/format";

type Account = {
  plan: "free" | "pro";
  limit: number;
  used_today: number;
  remaining: number;
  history_days: number;
  billing_available: boolean;
  subscription: { id: string; status: string; current_end: string | null; cancel_at_period_end: boolean } | null;
  history: { id: number; question: string; status: string; asked_at: string }[];
};

declare global {
  interface Window {
    Razorpay?: new (options: Record<string, unknown>) => { open: () => void };
  }
}

const CHECKOUT_JS = "https://checkout.razorpay.com/v1/checkout.js";

function loadCheckout(): Promise<void> {
  if (window.Razorpay) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = CHECKOUT_JS;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("Razorpay checkout could not be loaded"));
    document.body.appendChild(s);
  });
}

const STATUS_TEXT: Record<string, string> = {
  answered: "answered",
  needs_clarification: "needed clarification",
  unsupported: "not covered",
  blocked: "no answer shown",
  failed: "could not be answered",
};

export function AccountView() {
  const { configured, ready, user, openSignIn } = useAuth();
  const [acct, setAcct] = useState<Account | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const api = useCallback(
    async (path: string, method = "GET") => {
      const res = await fetch(path, { method, headers: { Authorization: `Bearer ${await user!.getIdToken()}` } });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `request failed (${res.status})`);
      return body;
    },
    [user],
  );

  const reload = useCallback(() => api("/api/account").then(setAcct).catch((e: Error) => setMessage(e.message)), [api]);

  useEffect(() => {
    if (user) reload();
  }, [user, reload]);

  if (!configured) return <p className="text-muted">Accounts are not switched on yet.</p>;
  if (!ready) return null;
  if (!user) {
    return (
      <div className="card mx-auto max-w-md p-8 text-center">
        <h1 className="text-3xl font-semibold tracking-tight">Your account</h1>
        {message && <p role="status" className="mt-2">{message}</p>}
        <p className="mt-2 text-muted">Sign in to see your plan, your allowance and your past questions.</p>
        <button type="button" onClick={openSignIn} className="mt-6 rounded-full bg-accent px-5 py-2.5 text-sm font-medium text-white hover:opacity-90">
          Sign in
        </button>
      </div>
    );
  }

  async function upgrade() {
    setBusy(true);
    setMessage(null);
    try {
      const { key_id, subscription_id } = await api("/api/billing/subscribe", "POST");
      await loadCheckout();
      new window.Razorpay!({
        key: key_id,
        subscription_id,
        name: "EdgeOS Pro",
        description: "Monthly subscription",
        prefill: { email: user!.email ?? "" },
        handler: () => {
          setMessage("Payment received. Pro switches on as soon as Razorpay confirms it, usually within a minute.");
          [3000, 10000, 30000].forEach((ms) => setTimeout(reload, ms)); // the webhook, not the browser, turns Pro on
        },
      }).open();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    if (!window.confirm("Cancel Pro? It stays on until the end of the month you have paid for.")) return;
    try {
      await api("/api/billing/cancel", "POST");
      await reload();
      setMessage("Pro is cancelled and will end at the close of the paid period.");
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  async function remove() {
    const typed = window.prompt("This deletes your questions and answers and your sign-in. Type DELETE to confirm.");
    if (typed !== "DELETE") return;
    try {
      await api("/api/account", "DELETE");
      await deleteUser(user!);
      setMessage("Your account has been deleted.");
    } catch (e) {
      const code = (e as { code?: string }).code;
      setMessage(code === "auth/requires-recent-login"
        ? "Your questions were deleted. To delete your sign-in too, sign out, sign in again and repeat this step."
        : (e as Error).message);
    }
  }

  const sub = acct?.subscription;
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight">Your account</h1>
        <p className="mt-1 text-sm text-muted">Signed in as {user.email ?? user.displayName}</p>
      </header>
      {message && <p role="status" className="card px-4 py-3 text-sm">{message}</p>}

      {acct && (
        <>
          <section aria-labelledby="plan-title" className="card p-6">
            <h2 id="plan-title" className="flex items-center gap-2 font-semibold">Plan <span className={`rounded-full px-2.5 py-0.5 text-xs ${acct.plan === "pro" ? "bg-accent text-white" : "bg-surface-2 text-muted"}`}>{acct.plan === "pro" ? "Pro" : "Free"}</span></h2>
            <div className="mt-4">
              <div className="flex justify-between text-xs text-muted">
                <span>Questions today</span>
                <span className="font-mono">{acct.used_today} / {acct.limit}</span>
              </div>
              <div className="mt-2 h-2 overflow-hidden rounded-full bg-surface-2">
                <div className="h-full rounded-full bg-accent" style={{ width: `${Math.min(100, (100 * acct.used_today) / acct.limit)}%` }} />
              </div>
              <p className="mt-2 text-xs text-muted">Resets at midnight IST</p>
            </div>
            {acct.plan === "pro" && sub?.current_end && (
              <p className="mt-1 text-sm text-muted">
                {sub.cancel_at_period_end ? "Ends" : "Renews"} on {formatDate(sub.current_end)}.
              </p>
            )}
            <div className="mt-5 flex flex-wrap gap-3">
              {acct.plan === "free" &&
                (acct.billing_available ? (
                  <button type="button" disabled={busy} onClick={upgrade} className="rounded-full bg-accent px-5 py-2.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40">
                    Upgrade to Pro, ₹399 a month
                  </button>
                ) : (
                  <p className="text-sm text-muted">Pro is not available to buy yet.</p>
                ))}
              {acct.plan === "pro" && !sub?.cancel_at_period_end && (
                <button type="button" onClick={cancel} className="rounded-full border border-line px-5 py-2.5 text-sm hover:bg-surface-2">Cancel Pro</button>
              )}
              <Link href="/pricing" className="self-center text-sm text-accent underline underline-offset-4">Compare plans</Link>
            </div>
          </section>

          <section aria-labelledby="history-title">
            <h2 id="history-title" className="mb-3 font-semibold">Your questions, last {acct.history_days} days</h2>
            {acct.history.length === 0 ? (
              <p className="text-sm text-muted">
                None yet. <Link href="/ask" className="text-accent underline underline-offset-4">Ask a question</Link>.
              </p>
            ) : (
              <ul className="card divide-y divide-line overflow-hidden">
                {acct.history.map((h) => (
                  <li key={h.id} className="flex flex-wrap items-baseline justify-between gap-2 px-4 py-3 text-sm">
                    <Link href={`/ask?id=${h.id}`} className="min-w-0 flex-1 truncate hover:text-accent">{h.question}</Link>
                    <span className="text-xs text-muted">{STATUS_TEXT[h.status] ?? h.status} · {formatDate(h.asked_at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section aria-labelledby="delete-title" className="card border-alert/30 p-6">
            <h2 id="delete-title" className="font-semibold">Delete your account</h2>
            <p className="mt-1 text-sm text-muted">
              Deletes your questions, answers and sign-in. Payment records are kept as tax law requires. Cancel Pro first.
            </p>
            <button type="button" onClick={remove} className="mt-4 rounded-full border border-alert/50 px-5 py-2.5 text-sm text-alert hover:bg-alert/10">
              Delete my account
            </button>
          </section>
        </>
      )}
    </div>
  );
}
