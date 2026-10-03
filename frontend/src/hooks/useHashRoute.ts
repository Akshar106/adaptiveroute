import { useSyncExternalStore } from "react";

export const TABS = ["query", "compare", "history", "traces", "benchmarks", "agents"] as const;
export type Tab = (typeof TABS)[number];

export interface Route {
  tab: Tab;
  param: string | null; // e.g. the trace id in #/traces/<id>
}

/** "#/traces/abc" -> { tab: "traces", param: "abc" }; anything unknown -> the query tab. */
export function parseHash(hash: string): Route {
  const [tab, ...rest] = hash.replace(/^#\/?/, "").split("/");
  const param = rest.length ? decodeURIComponent(rest.join("/")) : null;
  return (TABS as readonly string[]).includes(tab) ? { tab: tab as Tab, param: param || null } : { tab: "query", param: null };
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

export function useHashRoute(): Route {
  return parseHash(useSyncExternalStore(subscribe, () => window.location.hash));
}

export function href(tab: Tab, param?: string | null): string {
  return param ? `#/${tab}/${encodeURIComponent(param)}` : `#/${tab}`;
}
