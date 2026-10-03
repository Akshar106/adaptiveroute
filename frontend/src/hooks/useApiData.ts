import { useEffect, useState } from "react";

export type Loader<T> = (signal: AbortSignal) => Promise<T>;

interface State<T> {
  source?: Loader<T>;
  nonce?: number;
  data?: T;
  error?: unknown;
}

/**
 * Run `load` whenever its identity changes (callers wrap it in useCallback/useMemo
 * with the inputs as deps). While a new load is in flight the previous data is kept,
 * so views can dim the old render instead of flashing a skeleton; an error clears it.
 */
export function useApiData<T>(load: Loader<T> | null) {
  const [state, setState] = useState<State<T>>({});
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!load) return;
    const ctrl = new AbortController();
    load(ctrl.signal).then(
      (data) => setState({ source: load, nonce, data }),
      (error: unknown) => {
        if (!ctrl.signal.aborted) setState({ source: load, nonce, error });
      },
    );
    return () => ctrl.abort();
  }, [load, nonce]);

  const current = state.source === load && state.nonce === nonce;
  return {
    data: load ? state.data : undefined,
    error: current ? state.error : undefined,
    loading: load !== null && !current,
    reload: () => setNonce((n) => n + 1),
  };
}
