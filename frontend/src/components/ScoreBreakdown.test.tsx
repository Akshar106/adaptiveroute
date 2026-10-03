import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Candidate, Decision } from "../types";
import { breakdownKind, layoutBreakdown, PENALTY_TERMS, POSITIVE_TERMS, ScoreBreakdown } from "./ScoreBreakdown";

function adaptive(agent: string, c: Record<string, number>): Candidate {
  const score = Object.entries(c)
    .filter(([k]) => k.startsWith("contrib_"))
    .reduce((sum, [, v]) => sum + v, 0);
  return { agent, score, components: { eligible: 1, similarity: 0.8, ...c } };
}

const candidates = [
  adaptive("sql", { contrib_semantic: 0.45, contrib_success: 0.3, contrib_exploration: 0, contrib_latency: -0.04, contrib_cost: -0.02, contrib_load: -0.01 }),
  adaptive("code", { contrib_semantic: 0.2, contrib_success: 0.25, contrib_exploration: 0, contrib_latency: -0.06, contrib_cost: -0.05, contrib_load: 0 }),
];

const decision: Decision = {
  strategy: "adaptive",
  agent: "sql",
  fallback: null,
  reasoning: "sql scored highest",
  latency_ms: 3,
  cost_usd: 0,
  candidates,
};

describe("layoutBreakdown", () => {
  const rows = layoutBreakdown(candidates, [...POSITIVE_TERMS, ...PENALTY_TERMS]);

  it("contributions sum to each candidate's score", () => {
    for (const [i, row] of rows.entries()) {
      const sum = row.segments.reduce((s, seg) => s + seg.value, 0);
      expect(sum).toBeCloseTo(candidates[i]!.score, 10);
    }
  });

  it("stacks positives right of zero and penalties left of zero, without overlap", () => {
    const sql = rows[0]!;
    const pos = sql.segments.filter((s) => s.value > 0);
    const neg = sql.segments.filter((s) => s.value < 0);
    expect(pos.map((s) => [s.start, s.end])).toEqual([
      [0, 0.45],
      [0.45, 0.75],
    ]);
    expect(neg[0]).toMatchObject({ start: -0.04, end: 0 });
    expect(neg[1]!.end).toBeCloseTo(-0.04);
    expect(neg[1]!.start).toBeCloseTo(-0.06);
    expect(neg.every((s) => s.start < s.end && s.end <= 0)).toBe(true);
    expect(sql.segments.some((s) => s.term.key === "contrib_exploration")).toBe(false); // zero terms are skipped
  });
});

describe("ScoreBreakdown", () => {
  it("draws penalty segments left of the zero line and positive ones right of it", () => {
    const { container } = render(<ScoreBreakdown decision={decision} />);
    const zero = Number(screen.getByTestId("zero").getAttribute("x1"));
    // hBar paths start "M<baseline x>,<y>H<tip x>..."
    const xs = (el: Element) => {
      const m = /^M([\d.-]+),[\d.-]+H([\d.-]+)/.exec(el.getAttribute("d") ?? "");
      return [Number(m![1]), Number(m![2])];
    };
    const penalties = container.querySelectorAll('[data-term="contrib_latency"], [data-term="contrib_cost"]');
    const semantic = container.querySelectorAll('[data-term="contrib_semantic"]');
    expect(penalties.length).toBe(4); // 2 agents x 2 penalty terms
    expect(semantic.length).toBe(2);
    penalties.forEach((el) => expect(Math.max(...xs(el))).toBeLessThanOrEqual(zero));
    semantic.forEach((el) => expect(Math.min(...xs(el))).toBeGreaterThanOrEqual(zero));
  });

  it("lists exact values in the table view", () => {
    render(<ScoreBreakdown decision={decision} />);
    expect(screen.getAllByText("−0.040").length).toBeGreaterThan(0);
    expect(screen.getByText(candidates[0]!.score.toFixed(3), { selector: "strong" })).toBeInTheDocument();
  });

  it("picks the right breakdown per strategy", () => {
    expect(breakdownKind(candidates)).toBe("adaptive");
    expect(breakdownKind([{ agent: "sql", score: 0.8, components: { similarity: 0.8 } }])).toBe("similarity");
    expect(breakdownKind([{ agent: "sql", score: 0.9, components: { confidence: 0.9 } }])).toBe("confidence");
    expect(breakdownKind([{ agent: "sql", score: 1, components: {} }])).toBe("none");
  });

  it("says there is nothing to break down for round robin", () => {
    render(<ScoreBreakdown decision={{ ...decision, strategy: "round_robin", candidates: [{ agent: "sql", score: 1, components: {} }] }} />);
    expect(screen.getByText(/no score to break down/)).toBeInTheDocument();
  });
});
