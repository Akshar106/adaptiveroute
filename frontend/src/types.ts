// Hand-written types for the fields the dashboard uses (a subset of each schema).
// Source of truth: docs/api/openapi.json, plus the benchmark report shape, which
// the schema leaves as a free-form object.

export const STRATEGIES = ["round_robin", "embedding", "llm", "adaptive"] as const;
export type Strategy = (typeof STRATEGIES)[number];

export type QueryStatus = "routed" | "queued" | "running" | "completed" | "failed";

/** RFC 9457 problem+json body, as produced by api/errors.py. */
export interface Problem {
  title: string;
  status?: number;
  detail?: string;
  request_id?: string | null;
  errors?: { loc: (string | number)[]; msg: string; type?: string }[];
  query_id?: string; // only on 504 "Execution timed out"
}

// --- queries -----------------------------------------------------------------

export interface QueryCreate {
  query: string;
  strategy?: Strategy | null;
  mode?: "sync" | "async";
  use_cache?: boolean;
  execute?: boolean;
}

export interface Candidate {
  agent: string;
  score: number;
  components: Record<string, number>;
}

export interface Decision {
  strategy: string;
  agent: string;
  fallback: string | null;
  reasoning: string;
  latency_ms: number;
  cost_usd: number;
  candidates: Candidate[];
}

export interface Outcome {
  success: boolean;
  source: string;
  detail: string | null;
  created_at: string;
}

export interface Execution {
  attempt_no: number;
  agent: string;
  model: string;
  status: string; // success | error | timeout
  error: string | null;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  llm_attempts: number;
  cache_hit: boolean;
  outcome: Outcome | null;
}

export interface QueryOut {
  id: string;
  query: string;
  status: QueryStatus;
  created_at: string;
  decision: Decision;
  executions: Execution[];
  answer: string | null;
  total_cost_usd: number;
  trace_id: string | null;
}

export interface QuerySummary {
  id: string;
  query: string;
  status: string;
  strategy: string;
  selected_agent: string; // the router's pick
  final_agent: string | null; // who answered (differs after a failover); null if not executed
  created_at: string;
  routing_latency_ms: number;
  execution_latency_ms: number | null;
  total_cost_usd: number;
  success: boolean | null;
}

export interface QueryList {
  items: QuerySummary[];
  next_cursor: string | null;
}

export interface CompareOut {
  query: string;
  agreement: boolean;
  embedding_latency_ms: number | null;
  decisions: Record<string, Decision>;
}

// --- catalog -----------------------------------------------------------------

export interface AgentOut {
  name: string;
  display_name: string;
  description: string;
  model: string;
  reasoning_effort: string | null;
  max_concurrency: number;
  inflight: number;
  stats: {
    executions: number;
    failed_executions: number;
    labelled: number;
    success_rate: number | null;
    p50_latency_ms: number | null;
    p95_latency_ms: number | null;
    mean_cost_usd: number | null;
    total_cost_usd: number;
  };
}

export interface StrategiesOut {
  strategies: { name: string; description: string; available: boolean; is_default: boolean }[];
  adaptive_config: Record<string, unknown>;
}

export interface ReadyOut {
  status: "ready" | "not_ready";
  checks: Record<string, string>;
  llm_configured: boolean;
}

// --- traces ------------------------------------------------------------------

export interface TraceSpan {
  span_id: string;
  parent_span_id: string | null;
  name: string;
  service: string;
  start_offset_ms: number;
  duration_ms: number;
  status: string; // ok | error
  attributes: Record<string, unknown>;
}

export interface TraceOut {
  trace_id: string;
  duration_ms: number;
  spans: TraceSpan[];
  ui_url: string | null;
}

// --- benchmarks --------------------------------------------------------------

export interface BenchmarkRun {
  id: string;
  status: "queued" | "running" | "completed" | "failed";
  created_at: string;
  finished_at: string | null;
  git_sha: string | null;
}

/** GET /v1/me: the caller's API key (used to show admin-only actions). */
export interface Me {
  api_key_id: string;
  name: string;
  role: "admin" | "user";
  rate_limit_per_minute: number | null;
}

export interface BenchmarkDetail extends BenchmarkRun {
  summary: Record<string, unknown> | null; // the report object below
  report_markdown: string | null;
  error: string | null;
}

export interface JobAccepted {
  id: string;
  status: string;
  links: Record<string, string>;
}

/** The report object stored in BenchmarkDetail.summary (see evaluation/report.py). */
export interface BenchmarkReport {
  run_id: string;
  created_at: string;
  strategies: Record<string, StrategyReport>;
  ablations?: Record<string, StrategyReport>;
  baselines: Record<string, Baseline>;
  learning_curves: Record<string, CurvePoint[]>;
  caveats: string[];
  provenance: Provenance;
}

export interface MeanCI {
  mean: number;
  ci95: [number, number];
}

export interface Percentiles {
  p50: number;
  p95: number;
  mean: number;
}

export interface DomainCell {
  n: number;
  routing_accuracy: number;
  task_success: number;
}

export interface StrategyReport {
  summary: {
    n_items: number;
    n_seeds: number;
    routing_accuracy: MeanCI;
    task_success: MeanCI;
    error_rate: MeanCI;
    total_latency_ms: Percentiles;
    routing_latency_ms: Percentiles;
    cost_usd: { per_1k_queries: number };
  };
  by_domain: Record<string, DomainCell>;
}

export interface Baseline {
  task_success: number;
  routing_accuracy: number;
  mean_cost_usd: number;
  mean_execution_ms: number;
  error_rate: number;
}

export interface CurvePoint {
  position: number;
  rolling_success: number;
  rolling_accuracy: number;
}

export interface Provenance {
  git_sha?: string | null;
  git_dirty?: boolean;
  dataset_sha256?: string;
  dataset_items?: number;
  prices_as_of?: string;
  seeds?: number[];
  embedding_model?: string;
  llm_provider?: string;
}
