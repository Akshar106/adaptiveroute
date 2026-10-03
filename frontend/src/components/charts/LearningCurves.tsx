import { useState, type PointerEvent } from "react";
import { pct } from "../../format";
import { useElementWidth } from "../../hooks/useElementWidth";
import { strategyColor } from "../../palette";
import type { BenchmarkReport, CurvePoint } from "../../types";
import { ChartTable } from "./ChartTable";
import { linear, ticks } from "./scale";
import { Legend, TipRow, Tooltip, useTooltip } from "./Tooltip";

const M = { top: 14, right: 100, bottom: 34, left: 44 };
const H = 260;

/** Rolling task success as each strategy works through the query stream (crosshair tooltip). */
export function LearningCurves({ report }: { report: BenchmarkReport }) {
  const [box, width] = useElementWidth<HTMLDivElement>();
  const { ref, tip, show, hide } = useTooltip();
  const [hover, setHover] = useState<number | null>(null);
  const series = Object.entries(report.learning_curves).filter(([, pts]) => pts.length > 0);
  const positions = [...new Set(series.flatMap(([, pts]) => pts.map((p) => p.position)))].sort((a, b) => a - b);
  if (series.length === 0) return <p className="muted">No learning curves in this report.</p>;

  const xMax = Math.max(...positions, 1);
  const x = linear([0, xMax], [M.left, width - M.right]);
  const y = linear([0, 1], [H - M.bottom, M.top]);
  const at = (pts: CurvePoint[], pos: number) => pts.find((p) => p.position === pos);

  const readout = (pos: number) => (
    <>
      <div className="muted">position {pos}</div>
      {series.map(([name, pts]) => (
        <TipRow key={name} color={strategyColor(name)} value={pct(at(pts, pos)?.rolling_success)} label={name} />
      ))}
    </>
  );
  const onMove = (e: PointerEvent<SVGRectElement>) => {
    const svgX = e.clientX - e.currentTarget.getBoundingClientRect().left + M.left;
    const nearest = positions.reduce((best, p) => (Math.abs(x(p) - svgX) < Math.abs(x(best) - svgX) ? p : best));
    setHover(nearest);
    show(e, readout(nearest));
  };

  // Direct end labels. Lines that end at (nearly) the same point share one stacked label,
  // so a label never sits beside a line it does not belong to.
  const endLabels: { x: number; y: number; names: string[] }[] = [];
  for (const [name, pts] of series) {
    const last = pts[pts.length - 1]!;
    const ex = x(last.position);
    const ey = y(last.rolling_success);
    const near = endLabels.find((l) => Math.abs(l.x - ex) < 40 && Math.abs(l.y - ey) < 13);
    if (near) near.names.push(name);
    else endLabels.push({ x: ex, y: ey, names: [name] });
  }
  return (
    <div ref={box}>
      <Legend items={series.map(([name]) => ({ label: name, color: strategyColor(name), shape: "line" }))} />
      <div className="chart" ref={ref}>
        <svg width={width} height={H} role="img" aria-label="Learning curves: rolling task success by position">
          {[0, 0.25, 0.5, 0.75, 1].map((t) => (
            <g key={t}>
              <line className="gridline" x1={M.left} x2={width - M.right} y1={y(t)} y2={y(t)} />
              <text x={M.left - 6} y={y(t) + 4} textAnchor="end">{pct(t, 0)}</text>
            </g>
          ))}
          {ticks(0, xMax, 5).map((t) => (
            <text key={t} x={x(t)} y={H - M.bottom + 16} textAnchor="middle">{t}</text>
          ))}
          <text x={width - M.right} y={H - 4} textAnchor="end">position in query stream →</text>
          {hover !== null && <line className="axisline" x1={x(hover)} x2={x(hover)} y1={M.top} y2={H - M.bottom} />}
          {series.map(([name, pts]) => {
            const last = pts[pts.length - 1]!;
            return (
              <g key={name}>
                <path
                  d={pts.map((p, i) => `${i ? "L" : "M"}${x(p.position)},${y(p.rolling_success)}`).join("")}
                  fill="none"
                  stroke={strategyColor(name)}
                  strokeWidth={2}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
                <circle cx={x(last.position)} cy={y(last.rolling_success)} r={4} fill={strategyColor(name)} stroke="var(--surface)" strokeWidth={2} />
              </g>
            );
          })}
          {endLabels.map((l) => (
            <text key={l.names.join()} x={l.x + 8} y={l.y + 4} className="label-strong">
              {l.names.map((n, i) => (
                <tspan key={n} x={l.x + 8} dy={i ? 13 : 0}>{n}</tspan>
              ))}
            </text>
          ))}
          <rect
            x={M.left}
            y={M.top}
            width={Math.max(0, width - M.left - M.right)}
            height={H - M.top - M.bottom}
            fill="transparent"
            tabIndex={0}
            aria-label="Learning curve readout"
            onPointerMove={onMove}
            onPointerLeave={() => {
              setHover(null);
              hide();
            }}
            onFocus={(e) => {
              const pos = positions[positions.length - 1]!;
              setHover(pos);
              show(e, readout(pos));
            }}
            onBlur={() => {
              setHover(null);
              hide();
            }}
          />
        </svg>
        <Tooltip tip={tip} />
      </div>
      <ChartTable
        head={["Position", ...series.map(([name]) => name)]}
        rows={positions.map((pos) => [pos, ...series.map(([, pts]) => pct(at(pts, pos)?.rolling_success))])}
      />
    </div>
  );
}
