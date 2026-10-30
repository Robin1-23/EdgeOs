/** A small trend line drawn from closes the API sent (drawing only: no value is derived or shown). */

/** Catmull-Rom spline through the points as SVG cubic curves, so a few points still read as a smooth trend. */
function smoothPath(pts: [number, number][]): string {
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const [p0, p1, p2, p3] = [pts[i - 1] ?? pts[i], pts[i], pts[i + 1], pts[i + 2] ?? pts[i + 1]];
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C${c1[0]},${c1[1]} ${c2[0]},${c2[1]} ${p2[0]},${p2[1]}`;
  }
  return d;
}

export function Sparkline({ values, up, className = "h-8 w-24" }: { values?: number[]; up: boolean; className?: string }) {
  if (!values || values.length < 2) return <span className={className} />;
  const min = Math.min(...values);
  const span = Math.max(...values) - min || 1;
  const pts = values.map((v, i) => [(i / (values.length - 1)) * 100, 27 - ((v - min) / span) * 22] as [number, number]);
  const line = smoothPath(pts);
  const color = up ? "var(--up)" : "var(--down)";
  const id = `spark-${up ? "u" : "d"}`;
  return (
    <svg viewBox="0 0 100 30" preserveAspectRatio="none" className={className} aria-hidden>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity="0.28" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${line} L100,30 L0,30 Z`} fill={`url(#${id})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="1.6" vectorEffect="non-scaling-stroke" strokeLinecap="round" />
    </svg>
  );
}
