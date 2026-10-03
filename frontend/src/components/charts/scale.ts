// Tiny SVG helpers shared by the hand-rolled charts.

/** Linear map from a data domain to a pixel range. */
export function linear([d0, d1]: [number, number], [r0, r1]: [number, number]): (v: number) => number {
  const k = d1 === d0 ? 0 : (r1 - r0) / (d1 - d0);
  return (v) => r0 + (v - d0) * k;
}

/** About `count` round tick values (1/2/2.5/5 x 10^n steps) covering [lo, hi]. */
export function ticks(lo: number, hi: number, count = 5): number[] {
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / count;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

/** Horizontal bar from x0 (baseline, square) to x1 (data end, 4px rounded). Works in both directions. */
export function hBar(x0: number, x1: number, y: number, h: number, round = true): string {
  const w = Math.abs(x1 - x0);
  const r = round ? Math.min(4, w, h / 2) : 0;
  const d = x1 >= x0 ? 1 : -1; // direction of growth
  const tip = x1 - d * r;
  return `M${x0},${y}H${tip}Q${x1},${y} ${x1},${y + r}V${y + h - r}Q${x1},${y + h} ${tip},${y + h}H${x0}Z`;
}
