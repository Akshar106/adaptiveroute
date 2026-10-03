import { signed } from "../format";
import { useElementWidth } from "../hooks/useElementWidth";
import { agentColor, tint } from "../palette";
import type { Candidate, Decision } from "../types";
import { hBar, linear, ticks } from "./charts/scale";
import { Legend, TipRow, Tooltip, useTooltip } from "./charts/Tooltip";

export interface Term {
  key: string;
  label: string;
  opacity: number;
}

// Adaptive score = sum of these weighted terms (routing/scoring.py). Each bar keeps
// its agent's hue; a term is identified by its side, its order outward from zero
// and its opacity step (mirrored in the legend, the tooltip and the table).
export const POSITIVE_TERMS: Term[] = [
  { key: "contrib_semantic", label: "semantic fit", opacity: 1 },
  { key: "contrib_success", label: "historical success", opacity: 0.6 },
  { key: "contrib_exploration", label: "exploration bonus", opacity: 0.3 },
];
export const PENALTY_TERMS: Term[] = [
  { key: "contrib_latency", label: "latency penalty", opacity: 1 },
  { key: "contrib_cost", label: "cost penalty", opacity: 0.6 },
  { key: "contrib_load", label: "load penalty", opacity: 0.3 },
];

export type BreakdownKind = "adaptive" | "similarity" | "confidence" | "none";

export function breakdownKind(candidates: Candidate[]): BreakdownKind {
  const c = candidates[0]?.components ?? {};
  if ("contrib_semantic" in c) return "adaptive";
  if ("similarity" in c) return "similarity";
  if ("confidence" in c) return "confidence";
  return "none"; // round_robin: scores are 1/0 with no components
}

export interface Segment {
  term: Term;
  value: number;
  start: number; // data units; start < end on both sides
  end: number;
}

export interface BreakdownRow {
  agent: string;
  score: number;
  eligible: boolean;
  segments: Segment[];
}

/** Stack each candidate's terms around zero: positives grow right from 0, negatives grow left. */
export function layoutBreakdown(candidates: Candidate[], terms: Term[]): BreakdownRow[] {
  return candidates.map((c) => {
    let pos = 0;
    let neg = 0;
    const segments: Segment[] = [];
    for (const term of terms) {
      const value = c.components[term.key] ?? 0;
      if (value > 0) {
        segments.push({ term, value, start: pos, end: pos + value });
        pos += value;
      } else if (value < 0) {
        segments.push({ term, value, start: neg + value, end: neg });
        neg += value;
      }
    }
    return { agent: c.agent, score: c.score, eligible: c.components.eligible !== 0, segments };
  });
}

const LABEL_W = 104;
const SCORE_W = 56;
const ROW_H = 28;
const BAR_H = 14;
const AXIS_H = 22;

export function ScoreBreakdown({ decision }: { decision: Decision }) {
  const kind = breakdownKind(decision.candidates);
  if (kind === "none") {
    return <p className="muted">Round-robin ignores the query, so there is no score to break down.</p>;
  }
  const terms =
    kind === "adaptive"
      ? [...POSITIVE_TERMS, ...PENALTY_TERMS]
      : [{ key: kind, label: kind === "similarity" ? "cosine similarity" : "router confidence", opacity: 1 }];
  const rows = layoutBreakdown(decision.candidates, terms);

  return (
    <div>
      {kind === "adaptive" ? (
        <>
          <Legend items={POSITIVE_TERMS.map((t) => ({ label: `${t.label} →`, color: "var(--ink-2)", opacity: t.opacity }))} />
          <Legend items={PENALTY_TERMS.map((t) => ({ label: `← ${t.label}`, color: "var(--ink-2)", opacity: t.opacity }))} />
        </>
      ) : (
        <p className="muted small">Bar length is the {terms[0]?.label} for each candidate agent.</p>
      )}
      <BreakdownChart rows={rows} selected={decision.agent} />
      <details>
        <summary className="small">Exact values</summary>
        <BreakdownTable rows={rows} terms={terms} selected={decision.agent} />
      </details>
    </div>
  );
}

function BreakdownChart({ rows, selected }: { rows: BreakdownRow[]; selected: string }) {
  const [box, width] = useElementWidth<HTMLDivElement>();
  const { ref, tip, markProps } = useTooltip();
  const lo = Math.min(0, ...rows.flatMap((r) => r.segments.map((s) => s.start)));
  const hi = Math.max(0, ...rows.flatMap((r) => r.segments.map((s) => s.end)));
  const x = linear([lo, hi > lo ? hi : lo + 1], [LABEL_W, width - SCORE_W]);
  const height = rows.length * ROW_H + AXIS_H;

  return (
    <div ref={box}>
      <div className="chart" ref={ref}>
        <svg width={width} height={height} role="img" aria-label="Score breakdown per candidate agent">
          {ticks(lo, hi, width < 480 ? 3 : 5).map((t) => (
            <g key={t}>
              <line className="gridline" x1={x(t)} x2={x(t)} y1={0} y2={height - AXIS_H} />
              <text x={x(t)} y={height - 6} textAnchor="middle">{t}</text>
            </g>
          ))}
          <line className="axisline" data-testid="zero" x1={x(0)} x2={x(0)} y1={0} y2={height - AXIS_H} />
          <text x={width} y={12} textAnchor="end">score</text>
          {rows.map((row, i) => {
            const y = i * ROW_H + (ROW_H - BAR_H) / 2 + 8;
            const sides = [row.segments.filter((s) => s.value > 0), row.segments.filter((s) => s.value < 0)];
            return (
              <g key={row.agent}>
                <rect x={0} y={y + 2} width={10} height={10} rx={2} fill={agentColor(row.agent)} />
                <text x={16} y={y + 11} className={row.agent === selected ? "label-strong" : undefined}>
                  {row.agent}
                  {row.agent === selected ? " ✓" : ""}
                  {row.eligible ? "" : " (full)"}
                </text>
                {sides.map((side) =>
                  side.map((s, j) => {
                    // the 2px surface gap: every segment after the first starts 2px further out
                    const gap = j === 0 ? 0 : 2;
                    const base = s.value > 0 ? x(s.start) + gap : x(s.end) - gap;
                    const end = s.value > 0 ? x(s.end) : x(s.start);
                    if (Math.abs(end - base) < 0.5 || (end - base) * s.value < 0) return null;
                    const tipContent = (
                      <>
                        <TipRow color={agentColor(row.agent)} value={signed(s.value)} label={s.term.label} />
                        <div className="muted">
                          {row.agent} · score {row.score.toFixed(3)}
                        </div>
                      </>
                    );
                    return (
                      <path
                        key={s.term.key}
                        className="mark"
                        data-term={s.term.key}
                        d={hBar(base, end, y, BAR_H, j === side.length - 1)}
                        style={{ fill: tint(agentColor(row.agent), s.term.opacity) }}
                        aria-label={`${row.agent}: ${s.term.label} ${signed(s.value)}`}
                        {...markProps(tipContent)}
                      />
                    );
                  }),
                )}
                <text x={width} y={y + 11} textAnchor="end" className="label-strong">{row.score.toFixed(3)}</text>
              </g>
            );
          })}
        </svg>
        <Tooltip tip={tip} />
      </div>
    </div>
  );
}

function BreakdownTable({ rows, terms, selected }: { rows: BreakdownRow[]; terms: Term[]; selected: string }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Agent</th>
            {terms.map((t) => (
              <th key={t.key} className="num">{t.label}</th>
            ))}
            <th className="num">Score</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.agent} className={r.agent === selected ? "active" : undefined}>
              <td>
                {r.agent}
                {r.agent === selected ? " ✓" : ""}
              </td>
              {terms.map((t) => {
                const seg = r.segments.find((s) => s.term.key === t.key);
                return (
                  <td key={t.key} className="num">{signed(seg?.value ?? 0)}</td>
                );
              })}
              <td className="num">
                <strong>{r.score.toFixed(3)}</strong>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
