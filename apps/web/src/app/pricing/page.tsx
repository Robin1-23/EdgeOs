import type { Metadata } from "next";
import Link from "next/link";
import { Icon } from "@/components/Icons";

export const metadata: Metadata = {
  title: "Plans",
  description: "EdgeOS is free for stock and fund pages and the overlap tool; Pro adds a larger daily allowance in the ask box.",
};

const PLANS = [
  {
    name: "Free",
    price: "₹0",
    period: "forever",
    items: ["Every stock and fund page", "Fund overlap tool", "5 questions a day", "30 days of history"],
    cta: "Start free",
    featured: false,
  },
  {
    name: "Pro",
    price: "₹399",
    period: "per month",
    items: ["Everything in Free", "50 questions a day", "12 months of history"],
    cta: "Upgrade",
    featured: true,
  },
];

export default function PricingPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-12 pt-6 sm:pt-10">
      <header className="text-center">
        <h1 className="text-4xl font-semibold leading-[1.05] sm:text-6xl">
          Research is free.
          <span className="block text-muted/80">Pro is for daily askers.</span>
        </h1>
      </header>
      <ul className="grid gap-5 sm:grid-cols-2">
        {PLANS.map((p) => (
          <li
            key={p.name}
            className={`card relative flex flex-col p-8 ${p.featured ? "border-accent/50 shadow-[0_0_0_1px_rgba(198,241,53,0.5),0_30px_80px_-30px_rgba(198,241,53,0.35)]" : ""}`}
          >
            {p.featured && (
              <span className="absolute -top-3 left-7 rounded-full bg-accent px-3 py-1 text-xs font-medium text-accent-ink">Most useful</span>
            )}
            <h2 className="text-lg font-semibold">{p.name}</h2>
            <p className="mt-4 flex items-baseline gap-2">
              <span className="font-display text-6xl font-semibold tracking-tight">{p.price}</span>
              <span className="text-sm text-muted">{p.period}</span>
            </p>
            <ul className="mt-6 flex-1 space-y-3 text-sm">
              {p.items.map((i) => (
                <li key={i} className="flex items-center gap-3">
                  <span className={`flex h-5 w-5 items-center justify-center rounded-full ${p.featured ? "bg-accent text-accent-ink" : "bg-surface-2 text-foreground"}`}>
                    <Icon name="check" className="h-3 w-3" />
                  </span>
                  {i}
                </li>
              ))}
            </ul>
            <Link
              href="/account"
              className={`mt-8 px-4 py-3 text-sm ${p.featured ? "btn-primary" : "btn-ghost"}`}
            >
              {p.cta}
            </Link>
          </li>
        ))}
      </ul>
      <p className="text-center text-xs text-muted">
        Billed monthly via Razorpay (cards, UPI Autopay). Cancel any time. Pro never unlocks recommendations; EdgeOS doesn&apos;t give them.
      </p>
    </div>
  );
}
