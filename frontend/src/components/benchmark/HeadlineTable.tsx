import { ms, pct, usd, withCI } from "../../format";
import type { StrategyReport } from "../../types";

/** (a) One row per strategy: the numbers a reader compares first. */
export function HeadlineTable({ caption, strategies }: { caption: string; strategies: Record<string, StrategyReport> }) {
  const entries = Object.entries(strategies);
  const first = entries[0]?.[1].summary;
  return (
    <div>
      <h3>
        {caption}{" "}
        {first && (
          <span className="small muted" style={{ fontWeight: 400 }}>
            · {first.n_items} items × {first.n_seeds} seeds, 95% CI in parentheses
          </span>
        )}
      </h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Strategy</th>
              <th className="num">Routing accuracy</th>
              <th className="num">Task success</th>
              <th className="num">Latency P50 / P95</th>
              <th className="num">Routing P50 / P95</th>
              <th className="num">Cost / 1k queries</th>
              <th className="num">Error rate</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([name, { summary: s }]) => (
              <tr key={name}>
                <th scope="row">{name}</th>
                <td className="num">{withCI(s.routing_accuracy)}</td>
                <td className="num">{withCI(s.task_success)}</td>
                <td className="num">{`${ms(s.total_latency_ms.p50)} / ${ms(s.total_latency_ms.p95)}`}</td>
                <td className="num">{`${ms(s.routing_latency_ms.p50)} / ${ms(s.routing_latency_ms.p95)}`}</td>
                <td className="num">{usd(s.cost_usd.per_1k_queries)}</td>
                <td className="num">{pct(s.error_rate.mean)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
