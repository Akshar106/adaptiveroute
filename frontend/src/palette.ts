// Colour assignment. Categorical slots come from the dataviz palette (styles.css).
// Agents own slots 1-5 everywhere, so a hue always means the same agent.
// Charts about strategies (no agents on screen) use the remaining slots 6-8.

const AGENT_SLOTS: Record<string, number> = { code: 1, math: 2, sql: 3, writer: 4, knowledge: 5 };
export const AGENT_ORDER = Object.keys(AGENT_SLOTS);

export function agentColor(agent: string): string {
  const slot = AGENT_SLOTS[agent];
  return slot ? `var(--s${slot})` : "var(--ink-muted)";
}

// Fixed per strategy (never by rank), so a filtered chart never repaints a line.
const STRATEGY_SLOTS: Record<string, number> = { embedding: 6, adaptive: 7, adaptive_warm: 8 };

export function strategyColor(strategy: string): string {
  const slot = STRATEGY_SLOTS[strategy];
  return slot ? `var(--s${slot})` : "var(--ink-muted)";
}

/** `color` at `opacity` over the chart surface, but opaque (so gridlines never show through). */
export function tint(color: string, opacity = 1): string {
  return opacity >= 1 ? color : `color-mix(in srgb, ${color} ${Math.round(opacity * 100)}%, var(--surface))`;
}
