import { useState } from "react";
import { ms } from "../format";
import type { TraceOut, TraceSpan } from "../types";
import { ticks } from "./charts/scale";
import { Legend, TipRow, Tooltip, useTooltip } from "./charts/Tooltip";
import { StatusBadge } from "./StatusBadge";

export interface WaterfallRow {
  span: TraceSpan;
  depth: number;
  left: number; // % of the trace duration
  width: number; // % of the trace duration
}

/**
 * Order spans as a tree (each child right under its parent, siblings by start time)
 * and position them on one shared time axis. Depth comes from parent links; a span
 * whose parent is not in the trace is drawn as a root.
 */
export function layoutSpans(spans: TraceSpan[], durationMs: number): WaterfallRow[] {
  const ids = new Set(spans.map((s) => s.span_id));
  const children = new Map<string | null, TraceSpan[]>();
  for (const s of spans) {
    const parent = s.parent_span_id && ids.has(s.parent_span_id) ? s.parent_span_id : null;
    children.set(parent, [...(children.get(parent) ?? []), s]);
  }
  const total = Math.max(durationMs, ...spans.map((s) => s.start_offset_ms + s.duration_ms), 1e-9);
  const rows: WaterfallRow[] = [];
  const seen = new Set<string>();

  const add = (s: TraceSpan, depth: number) => {
    seen.add(s.span_id);
    rows.push({ span: s, depth, left: (s.start_offset_ms / total) * 100, width: (s.duration_ms / total) * 100 });
    const kids = [...(children.get(s.span_id) ?? [])].sort((a, b) => a.start_offset_ms - b.start_offset_ms);
    for (const k of kids) if (!seen.has(k.span_id)) add(k, depth + 1);
  };
  const roots = [...(children.get(null) ?? [])].sort((a, b) => a.start_offset_ms - b.start_offset_ms);
  for (const r of roots) add(r, 0);
  for (const s of spans) if (!seen.has(s.span_id)) add(s, 0); // only reachable through a parent cycle
  return rows;
}

export function Waterfall({ trace }: { trace: TraceOut }) {
  const rows = layoutSpans(trace.spans, trace.duration_ms);
  const total = Math.max(trace.duration_ms, ...trace.spans.map((s) => s.start_offset_ms + s.duration_ms));
  const [selected, setSelected] = useState<string | null>(null);
  const { ref, tip, show, hide } = useTooltip();
  const selectedSpan = trace.spans.find((s) => s.span_id === selected);
  const toggle = (id: string) => setSelected((cur) => (cur === id ? null : id));

  return (
    <div className="stack">
      <Legend
        items={[
          { label: "span", color: "var(--ink-muted)" },
          { label: "⚠ error span", color: "var(--critical)" },
        ]}
      />
      <div className="chart" ref={ref}>
        <div className="waterfall">
          <div className="small muted">Span</div>
          <div className="wf-axis" aria-hidden="true">
            {ticks(0, total, 4).map((t) => (
              // centre each label on its tick, except at the edges where it would overflow
              <span key={t} style={{ left: `${(t / total) * 100}%`, transform: t === 0 ? "none" : t / total > 0.9 ? "translateX(-100%)" : undefined }}>
                {ms(t)}
              </span>
            ))}
          </div>
          {rows.map(({ span, depth, left, width }) => {
            const error = span.status === "error";
            const tipContent = (
              <>
                <TipRow value={ms(span.duration_ms)} label={span.name} />
                <div className="muted">
                  {span.service} · starts at {ms(span.start_offset_ms)} · {error ? "⚠ error" : "ok"}
                </div>
              </>
            );
            return (
              <div key={span.span_id} className={`wf-row ${selected === span.span_id ? "wf-selected" : ""}`}>
                <div className="wf-name" style={{ paddingLeft: depth * 14 }}>
                  <button onClick={() => toggle(span.span_id)} aria-expanded={selected === span.span_id} title={span.name}>
                    {error && (
                      <span style={{ color: "var(--critical)" }} aria-label="error">
                        ⚠{" "}
                      </span>
                    )}
                    {span.name} <span className="muted">{ms(span.duration_ms)}</span>
                  </button>
                </div>
                <div className="wf-track" onClick={() => toggle(span.span_id)} onPointerMove={(e) => show(e, tipContent)} onPointerLeave={hide}>
                  <div className={`wf-bar ${error ? "error" : ""}`} style={{ left: `${left}%`, width: `${Math.min(width, 100 - left)}%` }} />
                </div>
              </div>
            );
          })}
        </div>
        <Tooltip tip={tip} />
      </div>
      {selectedSpan ? <SpanDetails span={selectedSpan} /> : <p className="small muted">Select a span to see its attributes.</p>}
    </div>
  );
}

function SpanDetails({ span }: { span: TraceSpan }) {
  const attributes = Object.entries(span.attributes);
  return (
    <section className="card">
      <h3>{span.name}</h3>
      <dl className="dl small">
        <dt>Status</dt>
        <dd>
          <StatusBadge status={span.status} />
        </dd>
        <dt>Service</dt><dd>{span.service}</dd>
        <dt>Start / duration</dt>
        <dd>
          {ms(span.start_offset_ms)} / {ms(span.duration_ms)}
        </dd>
        <dt>Span id</dt><dd className="mono">{span.span_id}</dd>
        {attributes.map(([k, v]) => (
          <div key={k} style={{ display: "contents" }}>
            <dt className="mono">{k}</dt><dd className="mono">{typeof v === "string" ? v : JSON.stringify(v)}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
