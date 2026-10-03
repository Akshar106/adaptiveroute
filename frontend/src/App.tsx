import { useState, useSyncExternalStore, type FormEvent } from "react";
import { rateLimit } from "./api";
import { ReadyIndicator } from "./components/ReadyIndicator";
import { useApiKey } from "./hooks/useApiKey";
import { href, TABS, useHashRoute, type Route, type Tab } from "./hooks/useHashRoute";
import { AgentsView } from "./views/AgentsView";
import { BenchmarksView } from "./views/BenchmarksView";
import { CompareView } from "./views/CompareView";
import { HistoryView } from "./views/HistoryView";
import { QueryView } from "./views/QueryView";
import { TracesView } from "./views/TracesView";

const TAB_LABELS: Record<Tab, string> = {
  query: "Query",
  compare: "Compare",
  history: "History",
  traces: "Traces",
  benchmarks: "Benchmarks",
  agents: "Agents",
};

function renderView({ tab, param }: Route, apiKey: string) {
  switch (tab) {
    case "query":
      return <QueryView apiKey={apiKey} queryId={param} />;
    case "compare":
      return <CompareView apiKey={apiKey} />;
    case "history":
      return <HistoryView apiKey={apiKey} selectedId={param} />;
    case "traces":
      return <TracesView key={param} apiKey={apiKey} traceId={param} />;
    case "benchmarks":
      return <BenchmarksView apiKey={apiKey} runId={param} />;
    case "agents":
      return <AgentsView apiKey={apiKey} />;
  }
}

export function App() {
  const [apiKey, setApiKey] = useApiKey();
  const route = useHashRoute();
  return (
    <>
      <header className="app-header">
        <div className="header-row">
          <h1 style={{ margin: 0 }}>AdaptiveRoute</h1>
          <ReadyIndicator />
          <span className="spacer" />
          <RateLimitLeft />
          <ApiKeyField value={apiKey} onSave={setApiKey} />
        </div>
        <nav className="tabs" aria-label="Views">
          {TABS.map((t) => (
            <a key={t} href={href(t)} aria-current={route.tab === t ? "page" : undefined}>{TAB_LABELS[t]}</a>
          ))}
        </nav>
      </header>
      {/* key: a different API key may see different data, so start every view fresh */}
      <main key={apiKey}>{apiKey ? renderView(route, apiKey) : <NoKeyNotice />}</main>
    </>
  );
}

function ApiKeyField({ value, onSave }: { value: string; onSave: (key: string) => void }) {
  const [draft, setDraft] = useState(value);
  const save = (e: FormEvent) => {
    e.preventDefault();
    onSave(draft.trim());
  };
  return (
    <form className="api-key row" onSubmit={save} style={{ gap: 6 }}>
      <label>
        API key
        <input type="password" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="ar_…" autoComplete="off" spellCheck={false} />
      </label>
      <button type="submit" disabled={draft.trim() === value}>Save</button>
      {value && (
        <button
          type="button"
          onClick={() => {
            setDraft("");
            onSave("");
          }}
        >
          Clear
        </button>
      )}
    </form>
  );
}

/** X-RateLimit-Remaining from the most recent API response. */
function RateLimitLeft() {
  const remaining = useSyncExternalStore(rateLimit.subscribe, rateLimit.get);
  if (remaining === null) return null;
  return (
    <span className="small muted" title="Requests left in the current rate-limit window">
      {remaining} requests left
    </span>
  );
}

function NoKeyNotice() {
  return (
    <div className="notice warning">
      <strong>Enter an API key to start</strong>
      <p>
        Every <code>/v1</code> call is authenticated with <code>Authorization: Bearer &lt;key&gt;</code>. Paste your key in the header bar; it
        is stored only in this browser's localStorage.
      </p>
      <p className="small muted">
        No key yet? Create one with <code>python -m adaptiveroute.cli create-api-key --name you --role admin</code>, or see the{" "}
        <a href="/docs">API docs</a>.
      </p>
    </div>
  );
}
