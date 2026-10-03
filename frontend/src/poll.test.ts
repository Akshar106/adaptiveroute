import { describe, expect, it, vi } from "vitest";
import { pollUntil, PollTimeoutError } from "./poll";

describe("pollUntil", () => {
  it("polls until done and reports every result", async () => {
    vi.useFakeTimers();
    const statuses = ["queued", "running", "completed"];
    const fetchOnce = vi.fn(async () => statuses.shift()!);
    const seen: string[] = [];
    const done = pollUntil(fetchOnce, (s) => s === "completed", { intervalMs: 1500, maxAttempts: 10, onUpdate: (s) => seen.push(s) });
    await vi.advanceTimersByTimeAsync(1500 * 3);
    await expect(done).resolves.toBe("completed");
    expect(seen).toEqual(["queued", "running", "completed"]);
    vi.useRealTimers();
  });

  it("gives up after maxAttempts", async () => {
    vi.useFakeTimers();
    const done = pollUntil(async () => "running", () => false, { intervalMs: 10, maxAttempts: 3 });
    const assertion = expect(done).rejects.toBeInstanceOf(PollTimeoutError);
    await vi.advanceTimersByTimeAsync(100);
    await assertion;
    vi.useRealTimers();
  });
});
