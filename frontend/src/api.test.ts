import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, parseRetryAfter, rateLimit, request } from "./api";

function mockFetch(body: unknown, init: ResponseInit & { contentType?: string } = {}) {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  const headers = new Headers(init.headers);
  headers.set("Content-Type", init.contentType ?? "application/json");
  const fn = vi.fn(async () => new Response(text, { ...init, headers }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

async function caught(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (err) {
    if (err instanceof ApiError) return err;
    throw err;
  }
  throw new Error("expected an ApiError");
}

afterEach(() => vi.unstubAllGlobals());

describe("request", () => {
  it("sends the bearer key and Idempotency-Key, and returns the status", async () => {
    const fetchFn = mockFetch({ id: "q1", status: "queued" }, { status: 202 });
    const res = await api.submitQuery("ar_key", { query: "hi", mode: "async" }, "idem-1");
    expect(res.status).toBe(202);
    expect(res.data).toMatchObject({ id: "q1" });
    const [url, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(url).toBe("/v1/queries");
    expect(headers.Authorization).toBe("Bearer ar_key");
    expect(headers["Idempotency-Key"]).toBe("idem-1");
    expect(JSON.parse(init.body as string)).toEqual({ query: "hi", mode: "async" });
  });

  it("turns problem+json into an ApiError with title, detail, request id and field errors", async () => {
    mockFetch(
      {
        title: "Request validation failed",
        status: 422,
        request_id: "req-123",
        errors: [{ loc: ["body", "query"], msg: "Field required", type: "missing" }],
      },
      { status: 422, contentType: "application/problem+json" },
    );
    const err = await caught(request("/v1/queries", { method: "POST", body: {} }));
    expect(err.status).toBe(422);
    expect(err.title).toBe("Request validation failed");
    expect(err.requestId).toBe("req-123");
    expect(err.problem.errors?.[0]?.msg).toBe("Field required");
  });

  it("reads Retry-After on 429 and falls back to the X-Request-ID header", async () => {
    mockFetch(
      { title: "Rate limit exceeded", status: 429, detail: "retry in 7s" },
      { status: 429, contentType: "application/problem+json", headers: { "Retry-After": "7", "X-Request-ID": "hdr-1" } },
    );
    const err = await caught(request("/v1/agents"));
    expect(err.retryAfterS).toBe(7);
    expect(err.detail).toBe("retry in 7s");
    expect(err.requestId).toBe("hdr-1");
  });

  it("handles a non-JSON error body (e.g. a proxy error page)", async () => {
    mockFetch("<html>Bad gateway</html>", { status: 502, statusText: "Bad Gateway", contentType: "text/html" });
    const err = await caught(request("/v1/agents"));
    expect(err.status).toBe(502);
    expect(err.title).toBe("Bad Gateway");
    expect(err.detail).toContain("Bad gateway");
  });

  it("reports a network failure as status 0", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    const err = await caught(request("/v1/agents"));
    expect(err.status).toBe(0);
    expect(err.title).toBe("Network error");
  });

  it("records X-RateLimit-Remaining for the header badge", async () => {
    mockFetch([], { status: 200, headers: { "X-RateLimit-Remaining": "41" } });
    await request("/v1/agents");
    expect(rateLimit.get()).toBe(41);
  });
});

describe("parseRetryAfter", () => {
  it("accepts seconds or an HTTP date", () => {
    const now = Date.parse("2026-10-03T12:00:00Z");
    expect(parseRetryAfter("3")).toBe(3);
    expect(parseRetryAfter("Sat, 03 Oct 2026 12:00:10 GMT", now)).toBe(10);
    expect(parseRetryAfter(null)).toBeNull();
  });
});
