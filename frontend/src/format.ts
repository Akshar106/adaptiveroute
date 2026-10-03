import type { MeanCI } from "./types";

const DASH = "—";

export function pct(v: number | null | undefined, digits = 1): string {
  return v == null ? DASH : `${(v * 100).toFixed(digits)}%`;
}

export function ms(v: number | null | undefined): string {
  if (v == null) return DASH;
  if (v === 0) return "0 ms";
  if (v >= 1000) return `${(v / 1000).toFixed(2)} s`;
  if (v >= 100) return `${v.toFixed(0)} ms`;
  if (v >= 1) return `${v.toFixed(1)} ms`;
  return `${v.toFixed(2)} ms`;
}

export function usd(v: number | null | undefined): string {
  if (v == null) return DASH;
  if (v === 0) return "$0";
  if (Math.abs(v) >= 0.01) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(6)}`;
}

export function signed(v: number, digits = 3): string {
  return `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(digits)}`;
}

export function int(v: number | null | undefined): string {
  return v == null ? DASH : v.toLocaleString();
}

export function dateTime(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString() : DASH;
}

/** "70.0% (40–100)": mean with its 95% confidence interval. */
export function withCI(m: MeanCI): string {
  return `${pct(m.mean)} (${pct(m.ci95[0], 0)}–${pct(m.ci95[1], 0)})`;
}

export function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

export function humanize(key: string): string {
  return key.replace(/_/g, " ");
}
