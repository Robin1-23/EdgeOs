// Presentation only: every value was computed by the agents; these functions never derive new numbers.

const grouped = (digits: number) =>
  new Intl.NumberFormat("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });

const sign = (v: number) => (v > 0 ? "+" : v < 0 ? "−" : "");

export function formatValue(value: number | string, unit: string | null): string {
  if (typeof value === "string") return value;
  if (!Number.isFinite(value)) return "—";
  switch (unit) {
    case "INR":
      return `${value < 0 ? "−" : ""}₹${grouped(2).format(Math.abs(value))}`;
    case "INR cr":
      return `${value < 0 ? "−" : ""}₹${grouped(Math.abs(value) >= 100 ? 0 : 1).format(Math.abs(value))} cr`;
    case "ratio":
      return `${(value * 100).toFixed(1)}%`;
    case "%":
      return `${value.toFixed(2)}%`;
    case "x":
      return `${value.toFixed(1)}×`;
    case "pp":
      return `${sign(value)}${Math.abs(value).toFixed(1)} pp`;
    case "points":
      return value.toFixed(0);
    case null:
      return Number.isInteger(value) ? grouped(0).format(value) : grouped(2).format(value);
    default:
      return `${grouped(2).format(value)} ${unit}`;
  }
}

export function formatChange(fraction: number | null): string {
  if (fraction === null) return "—";
  return `${sign(fraction)}${Math.abs(fraction * 100).toFixed(2)}%`;
}

/** A date ("2022-08-30") as is; a timestamp ("2022-08-29T18:30:00Z") as its date in India. */
export function formatDate(iso: string): string {
  const dateOnly = iso.length === 10;
  const d = new Date(dateOnly ? `${iso}T00:00:00Z` : iso);
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: dateOnly ? "UTC" : "Asia/Kolkata" });
}

/** DOM id for a citation such as "M1:close", "M3:flag:0" or "M5:finding:headline_6". */
export function anchorId(cite: string): string {
  return cite.replace(/[^A-Za-z0-9_-]/g, "-");
}

/** The date `months` before `iso` (YYYY-MM-DD), for chart ranges measured back from the report date. */
export function monthsBefore(iso: string, months: number): string {
  const [y, m, day] = iso.slice(0, 10).split("-").map(Number);
  const first = new Date(Date.UTC(y, m - 1 - months, 1));
  const lastDay = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0)).getUTCDate();
  first.setUTCDate(Math.min(day, lastDay)); // 30 Aug minus 6 months is 28 Feb, not 2 Mar
  return first.toISOString().slice(0, 10);
}
