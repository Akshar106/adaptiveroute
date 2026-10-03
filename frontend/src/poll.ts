/** Resolve after `ms`, or reject with the signal's reason when aborted. */
export function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(timer);
      reject(signal.reason);
    }, { once: true });
  });
}

export class PollTimeoutError extends Error {}

/**
 * Call `fetchOnce` every `intervalMs` until `isDone` says stop, reporting each
 * result through `onUpdate`. Gives up after `maxAttempts` (a cap, so a stuck
 * job can never poll forever).
 */
export async function pollUntil<T>(
  fetchOnce: () => Promise<T>,
  isDone: (value: T) => boolean,
  opts: { intervalMs: number; maxAttempts: number; signal?: AbortSignal; onUpdate?: (value: T) => void },
): Promise<T> {
  for (let attempt = 0; attempt < opts.maxAttempts; attempt++) {
    await sleep(opts.intervalMs, opts.signal);
    const value = await fetchOnce();
    opts.onUpdate?.(value);
    if (isDone(value)) return value;
  }
  throw new PollTimeoutError(`Gave up after ${opts.maxAttempts} checks`);
}
