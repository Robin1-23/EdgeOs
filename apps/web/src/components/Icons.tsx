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

/** The EdgeOS mark: a rising edge inside a rounded square. */
export function Logo({ className = "h-7 w-7" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden>
      <defs>
        <linearGradient id="edgeos-mark" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#8b9bff" />
          <stop offset="1" stopColor="#4f5ff0" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill="url(#edgeos-mark)" />
      <path d="M8 21.5l5-5 3.5 3.5L24 12" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
