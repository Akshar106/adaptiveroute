import { useCallback, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import { HeadlineTable } from "../components/benchmark/HeadlineTable";
import { BaselinesTable, CaveatsAndProvenance, DomainHeatmap } from "../components/benchmark/ReportTables";
import { LatencyBars } from "../components/charts/LatencyBars";
import { LearningCurves } from "../components/charts/LearningCurves";
import { SuccessCostScatter } from "../components/charts/SuccessCostScatter";
import { ErrorNotice } from "../components/ErrorNotice";
import { StatusBadge } from "../components/StatusBadge";
import { dateTime } from "../format";
import { useApiData } from "../hooks/useApiData";
import { href } from "../hooks/useHashRoute";
import type { BenchmarkDetail, BenchmarkReport, JobAccepted } from "../types";

/** `summary` is a free-form object in the schema; check the parts we draw before trusting it. */
export function asReport(summary: unknown): BenchmarkReport | null {
  const s = summary as Partial<BenchmarkReport> | null;
  return s && typeof s.strategies === "object" && typeof s.baselines === "object" ? (s as BenchmarkReport) : null;
}

export function BenchmarksView({ apiKey, runId }: { apiKey: string; runId: string | null }) {
  const loadList = useCallback((signal: AbortSignal) => api.listBenchmarks(apiKey, signal), [apiKey]);
  const list = useApiData(loadList);
  const loadMe = useCallback((signal: AbortSignal) => api.me(apiKey, signal), [apiKey]);
  const me = useApiData(loadMe);
  // With no run in the URL, show the newest completed one.
  const shownId = runId ?? list.data?.find((r) => r.status === "completed")?.id ?? null;
  const loadRun = useMemo(() => (shownId ? (signal: AbortSignal) => api.getBenchmark(apiKey, shownId, signal) : null), [apiKey, shownId]);
  const run = useApiData(loadRun);

  return (
    <div className="stack">
      <section className="card stack">
        <div className="row">
          <h2 style={{ margin: 0 }}>Benchmark runs</h2>
          <button onClick={list.reload}>Refresh</button>
          {me.data?.role === "admin" && <RunBenchmark apiKey={apiKey} />}
        </div>
        <ErrorNotice error={list.error} onRetry={list.reload} />
        {list.data?.length === 0 && <p className="muted">No benchmark runs stored yet.</p>}
        {list.data && list.data.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th>Finished</th>
                  <th>Git SHA</th>
                </tr>
              </thead>
              <tbody>
                {list.data.map((r) => (
                  <tr key={r.id} className={`clickable ${r.id === shownId ? "active" : ""}`} onClick={() => (window.location.hash = href("benchmarks", r.id))}>
                    <td>
                      <a href={href("benchmarks", r.id)} className="mono">{r.id}</a>
                    </td>
                    <td>
                      <StatusBadge status={r.status} />
                    </td>
                    <td className="small">{dateTime(r.created_at)}</td>
                    <td className="small">{dateTime(r.finished_at)}</td>
                    <td className="mono">{r.git_sha?.slice(0, 10) ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <ErrorNotice error={run.error} onRetry={run.reload} />
      {run.loading && !run.data && shownId && <p className="muted">Loading report…</p>}
      {run.data && (
        <div className={run.loading ? "refreshing" : undefined}>
          <BenchmarkReportView detail={run.data} />
        </div>
      )}
    </div>
  );
}

function RunBenchmark({ apiKey }: { apiKey: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [job, setJob] = useState<JobAccepted | null>(null);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      setJob(await api.startBenchmark(apiKey));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button onClick={start} disabled={busy} title="Admin only: replays the outcome matrix on the worker">
        {busy ? "Starting…" : "Run benchmark"}
      </button>
      {job && (
        <span className="small">
          Queued <code>{job.id}</code>; it appears in the list now and fills in when the worker finishes. Press Refresh.
        </span>
      )}
      {error instanceof ApiError && error.status === 403 ? (
        <span className="small muted">Running a benchmark needs an admin API key; you can still read every report.</span>
      ) : (
        <ErrorNotice error={error} />
      )}
    </>
  );
}

function BenchmarkReportView({ detail }: { detail: BenchmarkDetail }) {
  const [showRaw, setShowRaw] = useState(false);
  const report = asReport(detail.summary);
  if (!report) {
    return (
      <section className="card">
        <h2>
          Run <span className="mono">{detail.id}</span>
        </h2>
        <p>
          <StatusBadge status={detail.status} /> · {detail.status === "running" ? "The report appears when the run finishes." : "This run has no report."}
        </p>
        {detail.error && <pre className="raw">{detail.error}</pre>}
      </section>
    );
  }
  return (
    <div className="stack">
      <section className="card stack">
        <h2>
          Report <span className="mono">{detail.id}</span> <span className="small muted">· {dateTime(report.created_at)}</span>
        </h2>
        <HeadlineTable caption="Strategies" strategies={report.strategies} />
        {report.ablations && Object.keys(report.ablations).length > 0 && <HeadlineTable caption="Adaptive ablations" strategies={report.ablations} />}
      </section>
      <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 420px), 1fr))" }}>
        <section className="card">
          <h3>Task success vs cost</h3>
          <SuccessCostScatter report={report} />
        </section>
        <section className="card">
          <h3>End-to-end latency</h3>
          <LatencyBars report={report} />
        </section>
      </div>
      <section className="card stack">
        <h3>Per-domain results</h3>
        <DomainHeatmap report={report} metric="routing_accuracy" />
        <DomainHeatmap report={report} metric="task_success" />
      </section>
      <section className="card">
        <h3>Learning curves</h3>
        <LearningCurves report={report} />
      </section>
      <section className="card">
        <h3>Baselines</h3>
        <BaselinesTable report={report} />
      </section>
      <section className="card">
        <CaveatsAndProvenance report={report} />
      </section>
      {detail.report_markdown && (
        <section className="card stack">
          <button onClick={() => setShowRaw((v) => !v)} aria-expanded={showRaw}>
            {showRaw ? "Hide" : "Show"} raw report (Markdown)
          </button>
          {showRaw && <pre className="raw">{detail.report_markdown}</pre>}
        </section>
      )}
    </div>
  );
}
