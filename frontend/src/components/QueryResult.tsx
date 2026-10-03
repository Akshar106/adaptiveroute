import { useState } from "react";
import { api } from "../api";
import { dateTime, humanize, ms, usd } from "../format";
import { href } from "../hooks/useHashRoute";
import type { QueryOut } from "../types";
import { AgentTag } from "./AgentTag";
import { AnswerText } from "./AnswerText";
import { ErrorNotice } from "./ErrorNotice";
import { ExecutionsTable } from "./ExecutionsTable";
import { ScoreBreakdown } from "./ScoreBreakdown";
import { StatusBadge } from "./StatusBadge";

export function isTerminal(status: string): boolean {
  return status !== "queued" && status !== "running";
}

const NO_ANSWER: Record<string, string> = {
  routed: "Route only: the agent was not executed.",
  queued: "Waiting for a worker to pick up the query…",
  running: "The agent is running…",
  failed: "No answer: the agent run failed (see the executions below).",
};

interface Props {
  query: QueryOut;
  apiKey: string;
  onChange: (query: QueryOut) => void;
}

/** The full result of one query: answer, routing decision, score breakdown, executions. */
export function QueryResult({ query, apiKey, onChange }: Props) {
  const d = query.decision;
  const final = query.executions.at(-1);
  return (
    <div className="stack">
      <section className="card">
        <div className="row">
          <StatusBadge status={query.status} />
          <span>
            Agent <AgentTag agent={d.agent} />
          </span>
          {final && final.agent !== d.agent && <span className="badge">failed over to {final.agent}</span>}
          <span className="muted">strategy {humanize(d.strategy)}</span>
          {d.fallback && <span className="badge">router fallback: {d.fallback}</span>}
          <span>
            Total cost <strong>{usd(query.total_cost_usd)}</strong>
          </span>
          <span className="muted">routing {ms(d.latency_ms)}</span>
          {query.trace_id && <a href={href("traces", query.trace_id)}>View trace</a>}
        </div>
        <p className="small muted" style={{ marginTop: 8 }}>
          “{query.query}” · {dateTime(query.created_at)}
        </p>
      </section>

      <section className="card">
        <h2>Answer</h2>
        {query.answer ? <AnswerText text={query.answer} /> : <p className="muted">{NO_ANSWER[query.status] ?? "No answer."}</p>}
        {final && isTerminal(query.status) && <Feedback query={query} apiKey={apiKey} onChange={onChange} />}
      </section>

      <section className="card">
        <h2>Why this agent</h2>
        <p>{d.reasoning}</p>
        <ScoreBreakdown decision={d} />
      </section>

      <section className="card">
        <h2>Executions</h2>
        <ExecutionsTable executions={query.executions} />
      </section>
    </div>
  );
}

/** 👍/👎 labels the final execution; the label trains the adaptive router's success term. */
function Feedback({ query, apiKey, onChange }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const outcome = query.executions.at(-1)?.outcome;

  async function send(success: boolean) {
    setBusy(true);
    setError(null);
    try {
      onChange(await api.sendFeedback(apiKey, query.id, success));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack" style={{ marginTop: 12 }}>
      <div className="row">
        <span className="muted">Did this solve the task?</span>
        <button onClick={() => send(true)} disabled={busy} aria-label="Yes, it solved the task">
          👍 Yes
        </button>
        <button onClick={() => send(false)} disabled={busy} aria-label="No, it did not solve the task">
          👎 No
        </button>
        {outcome && (
          <span>
            Recorded <StatusBadge status={outcome.success ? "success" : "failure"} />{" "}
            <span className="small muted">
              ({outcome.source}, {dateTime(outcome.created_at)})
            </span>
          </span>
        )}
      </div>
      <ErrorNotice error={error} />
    </div>
  );
}
