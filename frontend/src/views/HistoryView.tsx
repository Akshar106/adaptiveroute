import { useCallback, useState } from "react";
import { api } from "../api";
import { AgentTag } from "../components/AgentTag";
import { ErrorNotice } from "../components/ErrorNotice";
import { QueryDetail } from "../components/QueryDetail";
import { StatusBadge } from "../components/StatusBadge";
import { dateTime, ms, truncate, usd } from "../format";
import { useApiData } from "../hooks/useApiData";
import { href } from "../hooks/useHashRoute";
import type { QuerySummary } from "../types";

export function HistoryView({ apiKey, selectedId }: { apiKey: string; selectedId: string | null }) {
  const load = useCallback((signal: AbortSignal) => api.listQueries(apiKey, null, signal), [apiKey]);
  const first = useApiData(load);
  // Pages fetched with "Load more" (keyset pagination: next_cursor is passed as `before`).
  const [more, setMore] = useState<{ items: QuerySummary[]; cursor?: string | null }>({ items: [] });
  const [moreBusy, setMoreBusy] = useState(false);
  const [moreError, setMoreError] = useState<unknown>(null);

  const items = [...(first.data?.items ?? []), ...more.items];
  const cursor = more.cursor !== undefined ? more.cursor : (first.data?.next_cursor ?? null);

  async function loadMore() {
    if (!cursor) return;
    setMoreBusy(true);
    setMoreError(null);
    try {
      const page = await api.listQueries(apiKey, cursor);
      setMore((m) => ({ items: [...m.items, ...page.items], cursor: page.next_cursor }));
    } catch (err) {
      setMoreError(err);
    } finally {
      setMoreBusy(false);
    }
  }

  function refresh() {
    setMore({ items: [] });
    first.reload();
  }

  return (
    <div className="stack">
      {selectedId && (
        <>
          <a href={href("history")}>← Back to list</a>
          <QueryDetail key={selectedId} apiKey={apiKey} queryId={selectedId} />
        </>
      )}
      <section className="card stack">
        <div className="row">
          <h2 style={{ margin: 0 }}>Recent queries</h2>
          <button onClick={refresh} disabled={first.loading}>Refresh</button>
        </div>
        <ErrorNotice error={first.error} onRetry={first.reload} />
        {first.loading && !first.data && <p className="muted">Loading…</p>}
        {first.data && items.length === 0 && <p className="muted">No queries yet. Submit one on the Query tab.</p>}
        {items.length > 0 && (
          <div className={`table-wrap ${first.loading ? "refreshing" : ""}`}>
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Query</th>
                  <th>Strategy</th>
                  <th>Agent</th>
                  <th>Status</th>
                  <th className="num">Routing</th>
                  <th className="num">Execution</th>
                  <th className="num">Cost</th>
                  <th>Success</th>
                </tr>
              </thead>
              <tbody>
                {items.map((q) => (
                  <tr
                    key={q.id}
                    className={`clickable ${q.id === selectedId ? "active" : ""}`}
                    onClick={() => (window.location.hash = href("history", q.id))}
                  >
                    <td className="small" style={{ whiteSpace: "nowrap" }}>{dateTime(q.created_at)}</td>
                    <td>
                      <a href={href("history", q.id)} title={q.query}>{truncate(q.query, 60)}</a>
                    </td>
                    <td>{q.strategy}</td>
                    <td>
                      <AgentTag agent={q.final_agent ?? q.selected_agent} />
                      {q.final_agent && q.final_agent !== q.selected_agent && (
                        <span className="small muted" title={`Router picked ${q.selected_agent}; it failed and ${q.final_agent} answered`}>
                          {" "}↪ failover
                        </span>
                      )}
                    </td>
                    <td>
                      <StatusBadge status={q.status} />
                    </td>
                    <td className="num">{ms(q.routing_latency_ms)}</td>
                    <td className="num">{ms(q.execution_latency_ms)}</td>
                    <td className="num">{usd(q.total_cost_usd)}</td>
                    <td>
                      {q.success === null ? <span className="muted">unlabelled</span> : <StatusBadge status={q.success ? "success" : "failure"} />}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <ErrorNotice error={moreError} />
        {cursor && (
          <button onClick={loadMore} disabled={moreBusy}>{moreBusy ? "Loading…" : "Load more"}</button>
        )}
      </section>
    </div>
  );
}
