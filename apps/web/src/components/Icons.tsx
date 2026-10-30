// A few line icons as inline SVG (paths from Lucide, ISC licence): one visual style, no icon dependency.

const PATHS: Record<string, string[]> = {
  search: ["M21 21l-4.3-4.3", "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14z"],
  arrow: ["M5 12h14", "M13 6l6 6-6 6"],
  info: ["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z", "M12 16v-4", "M12 8h.01"],
  check: ["M20 6 9 17l-5-5"],
  chart: ["M3 3v18h18", "M7 15l4-4 3 3 5-6"],
  layers: ["M12 2 2 7l10 5 10-5-10-5z", "M2 17l10 5 10-5", "M2 12l10 5 10-5"],
  sparkles: ["M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z", "M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"],
  send: ["M22 2 11 13", "M22 2l-7 20-4-9-9-4z"],
  up: ["M7 17 17 7", "M8 7h9v9"],
  down: ["M7 7l10 10", "M17 8v9H8"],
  alert: ["M12 9v4", "M12 17h.01", "M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"],
  user: ["M20 21a8 8 0 1 0-16 0", "M12 13a5 5 0 1 0 0-10 5 5 0 0 0 0 10z"],
  x: ["M18 6 6 18", "M6 6l12 12"],
  shield: ["M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"],
  scale: ["M12 3v18", "M5 7h14", "M5 7l-3 6a3 3 0 0 0 6 0z", "M19 7l-3 6a3 3 0 0 0 6 0z", "M8 21h8"],
  users: ["M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2", "M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", "M22 21v-2a4 4 0 0 0-3-3.9", "M16 3.1a4 4 0 0 1 0 7.8"],
};

export type IconName = keyof typeof PATHS;

export function Icon({ name, className = "h-4 w-4" }: { name: IconName; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
      {PATHS[name].map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}

/** The EdgeOS mark: a rising edge in an accent-coloured tile. */
export function Logo({ className = "h-7 w-7" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden>
      <rect width="32" height="32" rx="8" fill="var(--accent)" />
      <path d="M7.5 21.5l5.2-5.2 3.6 3.6L24.5 11.7" fill="none" stroke="var(--accent-ink)" strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="24.5" cy="11.7" r="2.2" fill="var(--accent-ink)" />
    </svg>
  );
}
