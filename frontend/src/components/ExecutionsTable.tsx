import { int, ms, usd } from "../format";
import type { Execution } from "../types";
import { AgentTag } from "./AgentTag";
import { StatusBadge } from "./StatusBadge";

/** One row per agent attempt (a failover adds a second attempt on another agent). */
export function ExecutionsTable({ executions }: { executions: Execution[] }) {
  if (executions.length === 0) return <p className="muted">No agent was executed (route only, or still queued).</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Agent</th>
            <th>Model</th>
            <th>Status</th>
            <th className="num">Latency</th>
            <th className="num">Tokens in / out</th>
            <th className="num">Cost</th>
            <th className="num">LLM attempts</th>
            <th>Cache</th>
          </tr>
        </thead>
        <tbody>
          {executions.map((e) => (
            <tr key={e.attempt_no}>
              <td className="num">{e.attempt_no}</td>
              <td>
                <AgentTag agent={e.agent} />
              </td>
              <td className="mono">{e.model}</td>
              <td>
                <StatusBadge status={e.status} />
                {e.error && <div className="small muted">{e.error}</div>}
              </td>
              <td className="num">{ms(e.latency_ms)}</td>
              <td className="num">
                {int(e.input_tokens)} / {int(e.output_tokens)}
              </td>
              <td className="num">{usd(e.cost_usd)}</td>
              <td className="num">{e.llm_attempts}</td>
              <td>{e.cache_hit ? "✓ hit" : "miss"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
