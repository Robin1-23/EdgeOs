"use client";

// The accent theme ("lime" default, "gold"): set on <html data-accent>, remembered on this device only.
import { useEffect, useState } from "react";

export type Accent = "lime" | "gold";
export const ACCENT_KEY = "edgeos.accent";
const EVENT = "edgeos-accent";

export function currentAccent(): Accent {
  return typeof document !== "undefined" && document.documentElement.dataset.accent === "gold" ? "gold" : "lime";
}

export function setAccent(a: Accent) {
  if (a === "gold") document.documentElement.dataset.accent = "gold";
  else delete document.documentElement.dataset.accent;
  try {
    localStorage.setItem(ACCENT_KEY, a);
  } catch {}
  window.dispatchEvent(new Event(EVENT));
}

/** Re-renders when the accent changes, so canvas charts can redraw in the new colours. */
export function useAccent(): Accent {
  const [accent, set] = useState<Accent>("lime");
  useEffect(() => {
    const sync = () => set(currentAccent());
    sync();
    window.addEventListener(EVENT, sync);
    return () => window.removeEventListener(EVENT, sync);
  }, []);
  return accent;
}

/** A CSS colour variable as an rgba() string a canvas chart understands. */
export function cssRgba(name: "--accent-rgb", alpha: number): string {
  const rgb = getComputedStyle(document.documentElement).getPropertyValue(name).trim().split(/\s+/).join(",");
  return `rgba(${rgb},${alpha})`;
}
