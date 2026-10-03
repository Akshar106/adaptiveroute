import { useEffect, useState } from "react";
import { fetchReady } from "../api";
import type { ReadyOut } from "../types";

/** /readyz status in the header: coloured dot plus a text label. Rechecks every 30s. */
export function ReadyIndicator() {
  const [ready, setReady] = useState<ReadyOut | null | undefined>(undefined); // undefined = first check pending

  useEffect(() => {
    const ctrl = new AbortController();
    const check = () =>
      fetchReady(ctrl.signal).then((r) => {
        if (!ctrl.signal.aborted) setReady(r);
      });
    check();
    const timer = setInterval(check, 30_000);
    return () => {
      ctrl.abort();
      clearInterval(timer);
    };
  }, []);

  const ok = ready?.status === "ready";
  const label = ready === undefined ? "Checking…" : ready === null ? "API unreachable" : ok ? "Ready" : "Not ready";
  const title = ready ? Object.entries(ready.checks).map(([k, v]) => `${k}: ${v}`).join("\n") : undefined;
  return (
    <span className="status" title={title} aria-live="polite">
      <span className="dot" aria-hidden="true" style={{ background: ready === undefined ? "var(--axis)" : ok ? "var(--good)" : "var(--critical)" }} />
      {label}
      {ready && !ready.llm_configured && <span className="muted small">(no LLM key)</span>}
    </span>
  );
}
