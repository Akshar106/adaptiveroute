import { useState, type FormEvent } from "react";
import { api } from "../api";
import { AgentTag } from "../components/AgentTag";
import { ErrorNotice } from "../components/ErrorNotice";
import { humanize, ms, usd } from "../format";
import type { CompareOut } from "../types";

export function CompareView({ apiKey }: { apiKey: string }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<CompareOut | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy || !text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await api.compare(apiKey, text.trim()));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <form className="card stack" onSubmit={submit}>
        <textarea rows={3} maxLength={4000} value={text} onChange={(e) => setText(e.target.value)} aria-label="Query to compare" placeholder="A query to route with every strategy" />
        <div className="row">
          <button type="submit" className="primary" disabled={busy || !text.trim()}>
            {busy ? "Comparing…" : "Compare strategies"}
          </button>
          <span className="small muted">Routes only, nothing is executed or stored. The LLM router call is billed as usual.</span>
        </div>
      </form>
      <ErrorNotice error={error} />
      {result && (
        <div className={`stack ${busy ? "refreshing" : ""}`}>
          <div className="row">
            <span className="badge">{result.agreement ? "✓ All strategies agree" : "≠ Strategies disagree"}</span>
            <span className="muted">
              Query embedding: <strong>{ms(result.embedding_latency_ms)}</strong> (computed once and shared, so it is not included in the latencies below)
            </span>
          </div>
          <div className="cards">
            {Object.entries(result.decisions).map(([name, d]) => (
              <section key={name} className="card">
                <h3>{humanize(name)}</h3>
                <p>
                  <AgentTag agent={d.agent} />
                  {d.fallback && <span className="badge"> fallback: {d.fallback}</span>}
                </p>
                <p className="small">{d.reasoning}</p>
                <dl className="dl small">
                  <dt>Latency</dt><dd>{ms(d.latency_ms)}</dd>
                  <dt>Cost</dt><dd>{usd(d.cost_usd)}</dd>
                </dl>
              </section>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
