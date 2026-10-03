import { pct } from "../../format";
import { useElementWidth } from "../../hooks/useElementWidth";
import type { BenchmarkReport } from "../../types";
import { ChartTable } from "./ChartTable";
import { linear, ticks } from "./scale";
import { Legend, TipRow, Tooltip, useTooltip } from "./Tooltip";

interface Point {
  name: string;
  success: number;
  ci: [number, number] | null;
  cost1k: number; // USD per 1,000 queries
  baseline: boolean;
}

export function successCostPoints(report: BenchmarkReport): Point[] {
  const points: Point[] = Object.entries(report.strategies).map(([name, r]) => ({
    name,
    success: r.summary.task_success.mean,
    ci: r.summary.task_success.ci95,
    cost1k: r.summary.cost_usd.per_1k_queries,
    baseline: false,
  }));
  for (const name of ["oracle", "label_oracle"]) {
    const b = report.baselines[name];
    if (b) points.push({ name, success: b.task_success, ci: null, cost1k: b.mean_cost_usd * 1000, baseline: true });
  }
  return points;
}

const M = { top: 26, right: 12, bottom: 40, left: 44 };
const H = 300;
const money = (v: number) => `$${Number(v.toPrecision(3))}`;

/** Task success (with 95% CI error bars) against cost. Identity is in direct text labels. */
export function SuccessCostScatter({ report }: { report: BenchmarkReport }) {
  const [box, width] = useElementWidth<HTMLDivElement>();
  const { ref, tip, markProps } = useTooltip();
  const points = successCostPoints(report);
  const xMax = Math.max(...points.map((p) => p.cost1k), 1e-9) * 1.15;
  const x = linear([0, xMax], [M.left, width - M.right]);
  const y = linear([0, 1], [H - M.bottom, M.top]);

  // Greedy label placement: right, left, below, above; first spot that overlaps nothing.
  const placed: number[][] = [];
  const labelFor = (p: Point) => {
    const px = x(p.cost1k);
    const py = y(p.success);
    const w = p.name.length * 6.3;
    const options: { x: number; y: number; anchor: "start" | "middle" | "end"; box: number[] }[] = [
      { x: px + 9, y: py + 4, anchor: "start", box: [px + 9, py - 8, px + 9 + w, py + 4] },
      { x: px - 9, y: py + 4, anchor: "end", box: [px - 9 - w, py - 8, px - 9, py + 4] },
      { x: px, y: py + 20, anchor: "middle", box: [px - w / 2, py + 9, px + w / 2, py + 21] },
      { x: px, y: py - 12, anchor: "middle", box: [px - w / 2, py - 23, px + w / 2, py - 11] },
    ];
    const free = (b: number[]) =>
      b[0]! >= 0 && b[2]! <= width && !placed.some((o) => b[0]! < o[2]! && b[2]! > o[0]! && b[1]! < o[3]! && b[3]! > o[1]!);
    const choice = options.find((o) => free(o.box)) ?? options[0]!;
    placed.push(choice.box);
    return choice;
  };

  return (
    <div ref={box}>
      <Legend
        items={[
          { label: "routing strategy (bar = 95% CI)", color: "var(--ink-2)" },
          { label: "oracle baseline", color: "var(--ink-2)", shape: "hollow" },
        ]}
      />
      <div className="chart" ref={ref}>
        <svg width={width} height={H} role="img" aria-label="Task success versus cost per strategy">
          {[0, 0.25, 0.5, 0.75, 1].map((t) => (
            <g key={t}>
              <line className="gridline" x1={M.left} x2={width - M.right} y1={y(t)} y2={y(t)} />
              <text x={M.left - 6} y={y(t) + 4} textAnchor="end">{pct(t, 0)}</text>
            </g>
          ))}
          {ticks(0, xMax, width < 480 ? 3 : 5).map((t) => (
            <text key={t} x={x(t)} y={H - M.bottom + 16} textAnchor="middle">{money(t)}</text>
          ))}
          <line className="axisline" x1={M.left} x2={width - M.right} y1={y(0)} y2={y(0)} />
          <text x={width - M.right} y={H - 4} textAnchor="end">cost per 1k queries (USD) →</text>
          <text x={M.left - 36} y={M.top - 14}>↑ task success</text>
          {points.map((p) => {
            const px = x(p.cost1k);
            const py = y(p.success);
            const label = labelFor(p);
            const content = (
              <>
                <TipRow value={pct(p.success)} label={`task success${p.ci ? ` (CI ${pct(p.ci[0], 0)}–${pct(p.ci[1], 0)})` : ""}`} />
                <TipRow value={money(p.cost1k)} label="per 1k queries" />
                <div className="muted">{p.name}</div>
              </>
            );
            return (
              <g key={p.name}>
                {p.ci && (
                  <g stroke="var(--ink-muted)" strokeWidth={1}>
                    <line x1={px} x2={px} y1={y(p.ci[0])} y2={y(p.ci[1])} />
                    <line x1={px - 4} x2={px + 4} y1={y(p.ci[0])} y2={y(p.ci[0])} />
                    <line x1={px - 4} x2={px + 4} y1={y(p.ci[1])} y2={y(p.ci[1])} />
                  </g>
                )}
                {p.baseline ? (
                  <circle cx={px} cy={py} r={5} fill="var(--surface)" stroke="var(--ink-2)" strokeWidth={1.5} />
                ) : (
                  <circle cx={px} cy={py} r={5} fill="var(--ink-2)" stroke="var(--surface)" strokeWidth={2} />
                )}
                <text x={label.x} y={label.y} textAnchor={label.anchor} className="label-strong">
                  {p.name}
                </text>
                <circle
                  className="mark"
                  cx={px}
                  cy={py}
                  r={12}
                  fill="transparent"
                  aria-label={`${p.name}: task success ${pct(p.success)}, ${money(p.cost1k)} per 1k queries`}
                  {...markProps(content)}
                />
              </g>
            );
          })}
        </svg>
        <Tooltip tip={tip} />
      </div>
      <ChartTable
        head={["Strategy / baseline", "Task success", "95% CI", "Cost per 1k queries"]}
        rows={points.map((p) => [p.name, pct(p.success), p.ci ? `${pct(p.ci[0])}–${pct(p.ci[1])}` : "—", money(p.cost1k)])}
      />
    </div>
  );
}
