import { useEffect, useState } from "react";
import { ApiError } from "../api";

const HINTS: Record<number, string> = {
  0: "Is the backend running? In development it is expected on http://localhost:8000.",
  401: "Check the API key in the header bar.",
  403: "This action needs an admin API key.",
};

/** Shows a problem+json error: title, detail, field errors and the request id to report. */
export function ErrorNotice({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null;
  if (!(error instanceof ApiError)) {
    return (
      <div className="notice error" role="alert">
        <strong>Something went wrong</strong>
        <p>{error instanceof Error ? error.message : String(error)}</p>
      </div>
    );
  }
  const limited = error.status === 429;
  return (
    <div className={`notice ${limited ? "warning" : "error"}`} role="alert">
      <strong>{error.title}</strong>
      {error.detail && <p>{error.detail}</p>}
      {HINTS[error.status] && <p className="muted">{HINTS[error.status]}</p>}
      {error.problem.errors && error.problem.errors.length > 0 && (
        <ul>
          {error.problem.errors.map((e, i) => (
            <li key={i}>
              <code>{e.loc.join(".")}</code>: {e.msg}
            </li>
          ))}
        </ul>
      )}
      {limited && <RetryCountdown key={error.requestId} seconds={error.retryAfterS} />}
      {onRetry && (
        <p>
          <button onClick={onRetry}>Retry</button>
        </p>
      )}
      {error.requestId && (
        <p className="small muted">
          Request ID <code>{error.requestId}</code>, quote it when reporting a problem.
        </p>
      )}
    </div>
  );
}

function RetryCountdown({ seconds }: { seconds: number | null }) {
  const [left, setLeft] = useState(seconds ?? 0);
  useEffect(() => {
    if (left <= 0) return;
    const timer = setTimeout(() => setLeft((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [left]);
  if (seconds == null) return <p>Rate limit reached. Wait a moment, then retry.</p>;
  return <p>{left > 0 ? `Rate limit reached. You can retry in ${left}s.` : "You can retry now."}</p>;
}
