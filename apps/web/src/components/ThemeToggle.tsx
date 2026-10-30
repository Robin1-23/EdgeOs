"use client";

import { type Accent, setAccent, useAccent } from "@/lib/theme";

const OPTIONS: { value: Accent; label: string; swatch: string }[] = [
  { value: "lime", label: "Lime theme", swatch: "#c6f135" },
  { value: "gold", label: "Gold theme", swatch: "#f2be48" },
];

/** Two swatches in the header: pick the accent colour of the whole site. */
export function ThemeToggle() {
  const accent = useAccent();
  return (
    <div role="radiogroup" aria-label="Colour theme" className="hidden items-center gap-1 rounded-full border border-line p-1 sm:flex">
      {OPTIONS.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={accent === o.value}
          aria-label={o.label}
          title={o.label}
          onClick={() => setAccent(o.value)}
          className={`flex h-6 w-6 items-center justify-center rounded-full transition-colors ${accent === o.value ? "bg-white/10" : "hover:bg-white/5"}`}
        >
          <span className="h-3 w-3 rounded-full" style={{ background: o.swatch, boxShadow: accent === o.value ? `0 0 10px ${o.swatch}` : "none" }} />
        </button>
      ))}
    </div>
  );
}
