import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Plans",
  description: "EdgeOS is free for stock and fund pages and the overlap tool; Pro adds a larger daily allowance in the ask box.",
};

const PLANS = [
  {
    name: "Free",
    price: "₹0",
    period: "",
    items: ["Every stock and fund page", "Fund overlap tool", "5 ask-box questions a day", "Your questions from the last 30 days"],
  },
  {
    name: "Pro",
    price: "₹399",
    period: "a month",
    items: ["Everything in Free", "50 ask-box questions a day", "Your questions from the last 12 months"],
  },
];

export default function PricingPage() {
  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Plans</h1>
        <p className="mt-1 max-w-2xl text-muted">
          Research pages are free for everyone. Pro is for people who use the ask box every day.
        </p>
      </header>
      <ul className="grid gap-4 sm:grid-cols-2">
        {PLANS.map((p) => (
          <li key={p.name} className="rounded-lg border border-line p-5">
            <h2 className="text-lg font-semibold">{p.name}</h2>
            <p className="mt-2">
              <span className="text-3xl font-semibold">{p.price}</span> <span className="text-muted">{p.period}</span>
            </p>
            <ul className="mt-4 space-y-1 text-sm">
              {p.items.map((i) => (
                <li key={i}>· {i}</li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
      <div className="space-y-2 text-sm text-muted">
        <p>Pro is billed monthly through Razorpay (cards and UPI Autopay). Cancel any time; Pro continues until the end of the month you paid for.</p>
        <p>
          The allowance resets at midnight IST. Both plans get the same research: Pro does not unlock recommendations, because
          EdgeOS never gives them.
        </p>
        <p>
          <Link href="/account" className="text-accent underline underline-offset-4">Go to your account</Link> to upgrade.
        </p>
      </div>
    </div>
  );
}
