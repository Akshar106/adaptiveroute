import { useMemo, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { ErrorNotice } from "../components/ErrorNotice";
import { Waterfall } from "../components/Waterfall";
import { ms } from "../format";
import { useApiData } from "../hooks/useApiData";
import { href } from "../hooks/useHashRoute";

export function TracesView({ apiKey, traceId }: { apiKey: string; traceId: string | null }) {
  const [input, setInput] = useState(traceId ?? "");
  const load = useMemo(() => (traceId ? (signal: AbortSignal) => api.getTrace(apiKey, traceId, signal) : null), [apiKey, traceId]);
  const { data, error, loading, reload } = useApiData(load);

  function submit(e: FormEvent) {
    e.preventDefault();
    const id = input.trim();
    if (id === traceId) reload();
    else if (id) window.location.hash = href("traces", id);
  }

  const status = error instanceof ApiError ? error.status : null;
  return (
    <div className="stack">
      <form className="card row" onSubmit={submit}>
        <label>
          Trace id
          <input type="text" value={input} onChange={(e) => setInput(e.target.value)} placeholder="32 hex characters" size={34} className="mono" />
        </label>
        <button type="submit" className="primary" disabled={!input.trim()}>Load trace</button>
      </form>

      {status === 404 ? (
        <div className="notice warning" role="alert">
          <strong>Trace not exported yet</strong>
          <p>Spans reach the trace backend a few seconds after a request finishes.</p>
          <button onClick={reload}>Retry</button>
          <RequestId error={error} />
        </div>
      ) : status === 503 ? (
        <div className="notice" role="alert">
          <strong>Trace backend not configured</strong>
          <p>The API has no trace query URL (AR_TRACE_QUERY_URL), so traces cannot be shown here.</p>
          <RequestId error={error} />
        </div>
      ) : (
        <ErrorNotice error={error} onRetry={reload} />
      )}

      {loading && !data && <p className="muted">Loading trace…</p>}
      {data && !error && (
        <section className={`card stack ${loading ? "refreshing" : ""}`}>
          <div className="row">
            <span className="mono small">{data.trace_id}</span>
            <span>
              Duration <strong>{ms(data.duration_ms)}</strong>
            </span>
            <span className="muted">{data.spans.length} spans</span>
            {data.ui_url && (
              <a href={data.ui_url} target="_blank" rel="noreferrer">Open in Jaeger ↗</a>
            )}
          </div>
          <Waterfall trace={data} />
        </section>
      )}
    </div>
  );
}

function RequestId({ error }: { error: unknown }) {
  if (!(error instanceof ApiError) || !error.requestId) return null;
  return (
    <p className="small muted">
      Request ID <code>{error.requestId}</code>
    </p>
  );
}
