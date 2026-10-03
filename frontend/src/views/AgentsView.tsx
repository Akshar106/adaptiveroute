import { useCallback } from "react";
import { api } from "../api";
import { AgentTag } from "../components/AgentTag";
import { ErrorNotice } from "../components/ErrorNotice";
import { StatusBadge } from "../components/StatusBadge";
import { humanize, int, ms, pct, usd } from "../format";
import { useApiData } from "../hooks/useApiData";

export function AgentsView({ apiKey }: { apiKey: string }) {
  const agents = useApiData(useCallback((signal: AbortSignal) => api.listAgents(apiKey, signal), [apiKey]));
  const strategies = useApiData(useCallback((signal: AbortSignal) => api.listStrategies(apiKey, signal), [apiKey]));
  const config = strategies.data?.adaptive_config ?? {};
  const weights = (config.weights ?? {}) as Record<string, number>;

  return (
    <div className="stack">
      <div className="row">
        <h2 style={{ margin: 0 }}>Agents</h2>
        <button onClick={agents.reload} disabled={agents.loading}>Refresh</button>
      </div>
      <ErrorNotice error={agents.error} onRetry={agents.reload} />
      {agents.loading && !agents.data && <p className="muted">Loading…</p>}
      <div className={`cards ${agents.loading ? "refreshing" : ""}`}>
        {agents.data?.map((a) => (
          <section key={a.name} className="card stack" style={{ gap: 8 }}>
            <div>
              <h3 style={{ marginBottom: 2 }}>
                <AgentTag agent={a.name} />
              </h3>
              <p className="small muted" style={{ margin: 0 }}>
                {a.display_name}: {a.description}
              </p>
            </div>
            <div>
              <div className="small">
                In flight {a.inflight} / {a.max_concurrency}
              </div>
              <div className="meter" role="meter" aria-valuemin={0} aria-valuemax={a.max_concurrency} aria-valuenow={a.inflight} aria-label="In-flight requests">
                <div style={{ width: `${Math.min(100, (a.inflight / Math.max(1, a.max_concurrency)) * 100)}%` }} />
              </div>
            </div>
            <dl className="dl small">
              <dt>Model</dt><dd className="mono">{a.model}</dd>
              <dt>Reasoning effort</dt><dd>{a.reasoning_effort ?? "—"}</dd>
              <dt>Executions</dt>
              <dd>
                {int(a.stats.executions)} ({int(a.stats.failed_executions)} failed)
              </dd>
              <dt>Success rate</dt>
              <dd>
                {pct(a.stats.success_rate)} <span className="muted">of {int(a.stats.labelled)} labelled</span>
              </dd>
              <dt>Latency P50 / P95</dt>
              <dd>
                {ms(a.stats.p50_latency_ms)} / {ms(a.stats.p95_latency_ms)}
              </dd>
              <dt>Cost mean / total</dt>
              <dd>
                {usd(a.stats.mean_cost_usd)} / {usd(a.stats.total_cost_usd)}
              </dd>
            </dl>
          </section>
        ))}
      </div>

      <h2 style={{ margin: "8px 0 0" }}>Routing strategies</h2>
      <ErrorNotice error={strategies.error} onRetry={strategies.reload} />
      {strategies.data && (
        <div className="cards">
          <section className="card">
            <div className="table-wrap">
              <table>
                <tbody>
                  {strategies.data.strategies.map((s) => (
                    <tr key={s.name}>
                      <th scope="row">
                        {s.name}
                        {s.is_default && <span className="badge"> default</span>}
                      </th>
                      <td>
                        <StatusBadge status={s.available ? "ok" : "failed"} label={s.available ? "available" : "unavailable"} />
                        <div className="small muted">{s.description}</div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section className="card">
            <h3>Adaptive scoring weights</h3>
            <p className="small muted">score = Σ weight × term; latency, cost and load are penalties.</p>
            <dl className="dl small">
              {Object.entries(weights).map(([k, v]) => (
                <div key={k} style={{ display: "contents" }}>
                  <dt>{humanize(k)}</dt><dd className="num" style={{ textAlign: "left" }}>{v}</dd>
                </div>
              ))}
              {Object.entries(config)
                .filter(([k]) => k !== "weights")
                .map(([k, v]) => (
                  <div key={k} style={{ display: "contents" }}>
                    <dt>{humanize(k)}</dt><dd>{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
                  </div>
                ))}
            </dl>
          </section>
        </div>
      )}
    </div>
  );
}
