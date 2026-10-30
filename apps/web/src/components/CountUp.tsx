"use client";

import { useEffect, useState } from "react";
import { formatValue } from "@/lib/format";

/** Counts up to `value` once on first view (about 0.8 s), then shows the exact formatted value. Static for reduced motion. */
export function CountUp({ value, unit, className }: { value: number; unit: string | null; className?: string }) {
  const [shown, setShown] = useState(value);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return setShown(value);
    let frame = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / 800);
      setShown(t < 1 ? value * (1 - Math.pow(1 - t, 3)) : value);
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value]);

  return (
    <span className={className} aria-label={formatValue(value, unit)}>
      <span aria-hidden>{formatValue(shown, unit)}</span>
    </span>
  );
}
