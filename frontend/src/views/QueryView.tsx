import { useRef, useState, type FormEvent } from "react";
import { api, ApiError, newIdempotencyKey } from "../api";
import { ErrorNotice } from "../components/ErrorNotice";
import { QueryDetail } from "../components/QueryDetail";
import { href } from "../hooks/useHashRoute";
import { STRATEGIES, type QueryCreate, type QueryOut, type Strategy } from "../types";

/** The shown result lives in the URL (#/query/<id>), so Back from a trace restores it. */
export function QueryView({ apiKey, queryId }: { apiKey: string; queryId: string | null }) {
  const [text, setText] = useState("");
  const [strategy, setStrategy] = useState<Strategy | "">("");
  const [mode, setMode] = useState<"sync" | "async">("sync");
  const [useCache, setUseCache] = useState(true);
  const [routeOnly, setRouteOnly] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<QueryOut | null>(null);
  const [timedOut, setTimedOut] = useState(false); // sync 504: the query keeps running
  // Idempotency-Key of the last unanswered request. Resubmitting the same body reuses
  // it, so retrying after a lost response replays the stored result instead of paying twice.
  const pending = useRef<{ body: string; key: string } | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy || !text.trim()) return; // double-click guard
    const body: QueryCreate = {
      query: text.trim(),
      strategy: strategy || null,
      mode,
      use_cache: useCache,
      execute: !routeOnly,
    };
    const fingerprint = JSON.stringify(body);
    if (pending.current?.body !== fingerprint) pending.current = { body: fingerprint, key: newIdempotencyKey() };

    setBusy(true);
    setError(null);
    setTimedOut(false);
    window.location.hash = href("query"); // hide the previous result while this one runs
    try {
      const res = await api.submitQuery(apiKey, body, pending.current.key);
      pending.current = null; // answered: the next submit is a new request
      setResult(res.data); // a 202 (async) result is polled by QueryDetail
      window.location.hash = href("query", res.data.id);
    } catch (err) {
      const timedOutId = err instanceof ApiError && err.status === 504 ? err.problem.query_id : undefined;
      if (timedOutId) {
        pending.current = null;
        setTimedOut(true); // the query exists and keeps running: follow it
        window.location.hash = href("query", timedOutId);
      } else {
        setError(err);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <form className="card stack" onSubmit={submit}>
        <textarea
          rows={4}
          maxLength={4000}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Ask something, e.g. “Write a SQL query that returns the top 5 customers by revenue”"
          aria-label="Query"
        />
        <div className="row">
          <label>
            Strategy
            <select value={strategy} onChange={(e) => setStrategy(e.target.value as Strategy | "")}>
              <option value="">server default</option>
              {STRATEGIES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </label>
          <label>
            Mode
            <select value={mode} onChange={(e) => setMode(e.target.value as "sync" | "async")}>
              <option value="sync">sync</option>
              <option value="async">async</option>
            </select>
          </label>
          <label>
            <input type="checkbox" checked={useCache} onChange={(e) => setUseCache(e.target.checked)} /> Use cache
          </label>
          <label>
            <input type="checkbox" checked={routeOnly} onChange={(e) => setRouteOnly(e.target.checked)} /> Route only
          </label>
          <button type="submit" className="primary" disabled={busy || !text.trim()}>
            {busy ? "Submitting…" : "Submit"}
          </button>
        </div>
      </form>
      <ErrorNotice error={error} />
      {timedOut && <div className="notice warning">The request timed out, but the query is still running. Following it below.</div>}
      {queryId && (
        <QueryDetail key={queryId} apiKey={apiKey} queryId={queryId} initial={result?.id === queryId ? result : undefined} />
      )}
    </div>
  );
}
