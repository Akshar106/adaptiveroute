import { ms } from "../../format";
import { useElementWidth } from "../../hooks/useElementWidth";
import type { BenchmarkReport } from "../../types";
import { ChartTable } from "./ChartTable";
import { hBar, linear, ticks } from "./scale";
import { Legend, TipRow, Tooltip, useTooltip } from "./Tooltip";

const LABEL_W = 112;
const ROW_H = 36;
const BAR_H = 12;
const AXIS_H = 20;
// P50 < P95 is an ordered pair, so it takes two steps of the ordinal blue ramp.
const SERIES = [
  { key: "p50", label: "P50", color: "var(--seq-300)" },
  { key: "p95", label: "P95", color: "var(--seq-600)" },
] as const;

/** End-to-end latency percentiles per strategy, value printed at each bar tip. */
export function LatencyBars({ report }: { report: BenchmarkReport }) {
  const [box, width] = useElementWidth<HTMLDivElement>();
  const { ref, tip, markProps } = useTooltip();
  const rows = Object.entries(report.strategies).map(([name, r]) => ({ name, ...r.summary.total_latency_ms }));
  const hi = Math.max(...rows.map((r) => r.p95), 1e-9);
  const x = linear([0, hi], [LABEL_W, width - 64]); // room on the right for tip labels
  const height = rows.length * ROW_H + AXIS_H;

  return (
    <div ref={box}>
      <Legend items={SERIES.map((s) => ({ label: s.label, color: s.color }))} />
      <div className="chart" ref={ref}>
        <svg width={width} height={height} role="img" aria-label="Latency P50 and P95 per strategy">
          {ticks(0, hi, width < 480 ? 3 : 5).map((t) => (
            <g key={t}>
              <line className="gridline" x1={x(t)} x2={x(t)} y1={0} y2={height - AXIS_H} />
              <text x={x(t)} y={height - 6} textAnchor="middle">{ms(t)}</text>
            </g>
          ))}
          <line className="axisline" x1={x(0)} x2={x(0)} y1={0} y2={height - AXIS_H} />
          {rows.map((r, i) => (
            <g key={r.name}>
              <text x={0} y={i * ROW_H + 21} className="label-strong">{r.name}</text>
              {SERIES.map((s, j) => {
                const y = i * ROW_H + 5 + j * (BAR_H + 2);
                const content = <TipRow color={s.color} value={ms(r[s.key])} label={`${s.label} · ${r.name}`} />;
                return (
                  <g key={s.key}>
                    <path
                      className="mark"
                      d={hBar(x(0), Math.max(x(r[s.key]), x(0) + 1), y, BAR_H)}
                      fill={s.color}
                      aria-label={`${r.name} ${s.label} ${ms(r[s.key])}`}
                      {...markProps(content)}
                    />
                    <text x={x(r[s.key]) + 4} y={y + BAR_H - 2}>{ms(r[s.key])}</text>
                  </g>
                );
              })}
            </g>
          ))}
        </svg>
        <Tooltip tip={tip} />
      </div>
      <ChartTable head={["Strategy", "P50", "P95", "Mean"]} rows={rows.map((r) => [r.name, ms(r.p50), ms(r.p95), ms(r.mean)])} />
    </div>
  );
}
