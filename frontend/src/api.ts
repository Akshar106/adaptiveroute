// Fetch wrapper for the AdaptiveRoute API: auth header, problem+json errors,
// Idempotency-Key, and the rate-limit headers. Same origin in production; the
// Vite dev server proxies /v1 to the backend.
import type {
  AgentOut,
  BenchmarkDetail,
  BenchmarkRun,
  CompareOut,
  JobAccepted,
  Problem,
  QueryCreate,
  QueryList,
  QueryOut,
  ReadyOut,
  StrategiesOut,
  TraceOut,
  Me,
} from "./types";

/** Any non-2xx response (or a network failure, status 0) becomes an ApiError. */
export class ApiError extends Error {
  readonly status: number;
  readonly title: string;
  readonly detail: string | undefined;
  readonly requestId: string | null;
  readonly problem: Partial<Problem>;
  readonly retryAfterS: number | null;

  constructor(status: number, problem: Partial<Problem>, requestId: string | null, retryAfterS: number | null) {
    super(problem.title ?? `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.title = problem.title ?? `HTTP ${status}`;
    this.detail = problem.detail;
    this.requestId = requestId;
    this.problem = problem;
    this.retryAfterS = retryAfterS;
  }
}

// --- rate-limit store (read by the header via useSyncExternalStore) ----------------

let remaining: number | null = null;
const listeners = new Set<() => void>();

export const rateLimit = {
  get: (): number | null => remaining,
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
};

function recordRateLimit(headers: Headers): void {
  const value = headers.get("X-RateLimit-Remaining");
  if (value === null || Number(value) === remaining) return;
  remaining = Number(value);
  listeners.forEach((l) => l());
}

/** Retry-After is either delay-seconds or an HTTP date. */
export function parseRetryAfter(value: string | null, now = Date.now()): number | null {
  if (!value) return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds)) return Math.max(0, Math.ceil(seconds));
  const date = Date.parse(value);
  return Number.isNaN(date) ? null : Math.max(0, Math.ceil((date - now) / 1000));
}

// --- core request ------------------------------------------------------------------

export interface RequestOptions {
  apiKey?: string;
  method?: "GET" | "POST";
  body?: unknown;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export interface ApiResult<T> {
  data: T;
  status: number;
  requestId: string | null;
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<ApiResult<T>> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (opts.apiKey) headers.Authorization = `Bearer ${opts.apiKey}`;
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";

  let res: Response;
  try {
    res = await fetch(path, {
      method: opts.method ?? "GET",
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal: opts.signal,
    });
  } catch (err) {
    if (opts.signal?.aborted) throw err; // callers ignore their own aborts
    throw new ApiError(0, { title: "Network error", detail: "Could not reach the API server." }, null, null);
  }

  recordRateLimit(res.headers);
  const requestId = res.headers.get("X-Request-ID");
  const payload = await readBody(res);
  if (!res.ok) {
    const problem = toProblem(res, payload);
    const retryAfter = parseRetryAfter(res.headers.get("Retry-After"));
    throw new ApiError(res.status, problem, problem.request_id ?? requestId, retryAfter);
  }
  if (typeof payload === "string") {
    // e.g. an HTML page because the dev proxy is not forwarding this path
    throw new ApiError(res.status, { title: "Unexpected response", detail: "Expected JSON from the API." }, requestId, null);
  }
  return { data: payload as T, status: res.status, requestId };
}

async function readBody(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  if ((res.headers.get("Content-Type") ?? "").includes("json")) {
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  }
  return text;
}

function toProblem(res: Response, payload: unknown): Partial<Problem> {
  const obj = typeof payload === "object" && payload !== null ? (payload as Record<string, unknown>) : null;
  if (obj && typeof obj.title === "string") return obj as Partial<Problem>;
  // Not problem+json (a proxy error page, or FastAPI's default {"detail": ...}).
  const detail = typeof obj?.detail === "string" ? obj.detail : typeof payload === "string" ? payload.slice(0, 300) : undefined;
  return { title: res.statusText || `HTTP ${res.status}`, status: res.status, detail };
}

/** crypto.randomUUID only exists in secure contexts (https or localhost). */
export function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
}

// --- endpoints -----------------------------------------------------------------------

const enc = encodeURIComponent;

async function get<T>(apiKey: string, path: string, signal?: AbortSignal): Promise<T> {
  return (await request<T>(path, { apiKey, signal })).data;
}

async function post<T>(apiKey: string, path: string, body: unknown): Promise<T> {
  return (await request<T>(path, { apiKey, method: "POST", body })).data;
}

export const api = {
  /** Returns the full result: a 202 status means async mode, so the caller polls. */
  submitQuery: (apiKey: string, body: QueryCreate, idempotencyKey: string) =>
    request<QueryOut>("/v1/queries", { apiKey, method: "POST", body, idempotencyKey }),
  getQuery: (apiKey: string, id: string, signal?: AbortSignal) => get<QueryOut>(apiKey, `/v1/queries/${enc(id)}`, signal),
  listQueries: (apiKey: string, before: string | null, signal?: AbortSignal) =>
    get<QueryList>(apiKey, `/v1/queries?limit=20${before ? `&before=${enc(before)}` : ""}`, signal),
  sendFeedback: (apiKey: string, id: string, success: boolean) =>
    post<QueryOut>(apiKey, `/v1/queries/${enc(id)}/feedback`, { success }),
  compare: (apiKey: string, query: string) => post<CompareOut>(apiKey, "/v1/route/compare", { query }),
  getTrace: (apiKey: string, traceId: string, signal?: AbortSignal) => get<TraceOut>(apiKey, `/v1/traces/${enc(traceId)}`, signal),
  listBenchmarks: (apiKey: string, signal?: AbortSignal) => get<BenchmarkRun[]>(apiKey, "/v1/benchmarks", signal),
  getBenchmark: (apiKey: string, id: string, signal?: AbortSignal) =>
    get<BenchmarkDetail>(apiKey, `/v1/benchmarks/${enc(id)}`, signal),
  startBenchmark: (apiKey: string) => post<JobAccepted>(apiKey, "/v1/benchmarks", {}),
  listAgents: (apiKey: string, signal?: AbortSignal) => get<AgentOut[]>(apiKey, "/v1/agents", signal),
  me: (apiKey: string, signal?: AbortSignal) => get<Me>(apiKey, "/v1/me", signal),
  listStrategies: (apiKey: string, signal?: AbortSignal) => get<StrategiesOut>(apiKey, "/v1/strategies", signal),
};

/** /readyz needs no auth and answers 503 (with the same body) when not ready. */
export async function fetchReady(signal?: AbortSignal): Promise<ReadyOut | null> {
  try {
    const res = await fetch("/readyz", { signal });
    return (await res.json()) as ReadyOut;
  } catch {
    return null;
  }
}
