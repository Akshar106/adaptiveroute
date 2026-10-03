import { useEffect, useState } from "react";
import { api } from "../api";
import { pollUntil, PollTimeoutError } from "../poll";
import type { QueryOut } from "../types";
import { ErrorNotice } from "./ErrorNotice";
import { isTerminal, QueryResult } from "./QueryResult";

const POLL_MS = 1500;
const MAX_POLLS = 80; // about two minutes

interface Props {
  apiKey: string;
  queryId: string;
  initial?: QueryOut; // already fetched (e.g. the POST response), skips the first GET
}

/**
 * Loads a query and, while it is queued or running, polls GET /v1/queries/{id}.
 * Parents mount it with key={queryId}, so a new id starts with fresh state.
 */
export function QueryDetail({ apiKey, queryId, initial }: Props) {
  const [query, setQuery] = useState<QueryOut | null>(initial ?? null);
  const [error, setError] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0); // bumped by "Check again"

  useEffect(() => {
    const ctrl = new AbortController();
    const fetchOnce = () => api.getQuery(apiKey, queryId, ctrl.signal);
    const first = attempt === 0 && initial ? Promise.resolve(initial) : fetchOnce();
    first
      .then((q) => {
        setQuery(q);
        if (isTerminal(q.status)) return q;
        return pollUntil(fetchOnce, (x) => isTerminal(x.status), {
          intervalMs: POLL_MS,
          maxAttempts: MAX_POLLS,
          signal: ctrl.signal,
          onUpdate: setQuery,
        });
      })
      .catch((err: unknown) => {
        if (!ctrl.signal.aborted) setError(err);
      });
    return () => ctrl.abort();
  }, [apiKey, queryId, initial, attempt]);

  const retry = () => {
    setError(null);
    setAttempt((n) => n + 1);
  };
  const polling = query !== null && !isTerminal(query.status) && !error;

  return (
    <div className="stack">
      {polling && (
        <p className="muted" aria-live="polite">
          ◔ Query is {query.status}; checking every {POLL_MS / 1000}s…
        </p>
      )}
      {error instanceof PollTimeoutError ? (
        <div className="notice warning">
          Still {query?.status} after two minutes. <button onClick={retry}>Check again</button>
        </div>
      ) : (
        <ErrorNotice error={error} onRetry={retry} />
      )}
      {query ? <QueryResult query={query} apiKey={apiKey} onChange={setQuery} /> : !error && <p className="muted">Loading…</p>}
    </div>
  );
}
