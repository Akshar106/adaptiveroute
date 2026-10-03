import { ms, pct, usd } from "../../format";
import { AGENT_ORDER } from "../../palette";
import type { BenchmarkReport, DomainCell } from "../../types";

const order = (d: string) => (AGENT_ORDER.includes(d) ? AGENT_ORDER.indexOf(d) : AGENT_ORDER.length);

/** Sequential ramp step 100..700 for a 0..1 value; text flips to white on the dark steps. */
function cellStyle(v: number) {
  const step = 100 + Math.round(Math.min(1, Math.max(0, v)) * 6) * 100;
  return { background: `var(--seq-${step})`, color: step >= 500 ? "#ffffff" : "#0b0b0b" };
}

/** (d) Heatmap table: one row per domain, one column per strategy, value printed in every cell. */
export function DomainHeatmap({ report, metric }: { report: BenchmarkReport; metric: "routing_accuracy" | "task_success" }) {
  const names = Object.keys(report.strategies);
  const cells = (name: string): Record<string, DomainCell> => report.strategies[name]?.by_domain ?? {};
  const domains = [...new Set(names.flatMap((n) => Object.keys(cells(n))))].sort((a, b) => order(a) - order(b));
  return (
    <div className="table-wrap">
      <table>
        <caption>{metric === "routing_accuracy" ? "Routing accuracy" : "Task success"} by domain</caption>
        <thead>
          <tr>
            <th>Domain</th>
            <th className="num">n</th>
            {names.map((n) => (
              <th key={n} className="num">{n}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {domains.map((d) => (
            <tr key={d}>
              <th scope="row">{d}</th>
              <td className="num">{names.map((n) => cells(n)[d]?.n).find((n) => n != null) ?? "—"}</td>
              {names.map((n) => {
                const v = cells(n)[d]?.[metric];
                return v == null ? (
                  <td key={n} className="num">—</td>
                ) : (
                  <td key={n} className="num heat" style={cellStyle(v)} title={`${n} · ${d}: ${pct(v)}`}>
                    {pct(v, 0)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="legend" aria-hidden="true">
        <span>
          0%
          {[100, 200, 300, 400, 500, 600, 700].map((s) => (
            <span key={s} className="swatch" style={{ background: `var(--seq-${s})` }} />
          ))}
          100%
        </span>
      </div>
    </div>
  );
}

/** (f) Fixed-policy and oracle baselines. */
export function BaselinesTable({ report }: { report: BenchmarkReport }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Baseline</th>
            <th className="num">Task success</th>
            <th className="num">Routing accuracy</th>
            <th className="num">Cost / 1k queries</th>
            <th className="num">Mean execution</th>
            <th className="num">Error rate</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(report.baselines).map(([name, b]) => (
            <tr key={name}>
              <th scope="row">{name}</th>
              <td className="num">{pct(b.task_success)}</td>
              <td className="num">{pct(b.routing_accuracy)}</td>
              <td className="num">{usd(b.mean_cost_usd * 1000)}</td>
              <td className="num">{ms(b.mean_execution_ms)}</td>
              <td className="num">{pct(b.error_rate)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** (g) Caveats and the provenance needed to reproduce the run. */
export function CaveatsAndProvenance({ report }: { report: BenchmarkReport }) {
  const p = report.provenance;
  return (
    <div className="cards">
      <div>
        <h3>Caveats</h3>
        <ul className="small" style={{ paddingLeft: 18, margin: 0 }}>
          {report.caveats.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      </div>
      <div>
        <h3>Provenance</h3>
        <dl className="dl small">
          <dt>Git SHA</dt>
          <dd className="mono">
            {p.git_sha?.slice(0, 12) ?? "—"}
            {p.git_dirty ? " (dirty)" : ""}
          </dd>
          <dt>Dataset SHA-256</dt><dd className="mono">{p.dataset_sha256?.slice(0, 12) ?? "—"}</dd>
          <dt>Dataset items</dt><dd>{p.dataset_items ?? "—"}</dd>
          <dt>Prices as of</dt><dd>{p.prices_as_of ?? "—"}</dd>
          <dt>Seeds</dt><dd>{p.seeds?.join(", ") ?? "—"}</dd>
          <dt>Embedding model</dt><dd>{p.embedding_model ?? "—"}</dd>
          <dt>LLM provider</dt><dd>{p.llm_provider ?? "—"}</dd>
        </dl>
      </div>
    </div>
  );
}
