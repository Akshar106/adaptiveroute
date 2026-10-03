import { useCallback, useRef, useState, type FocusEvent, type PointerEvent, type ReactNode } from "react";
import { tint } from "../../palette";

interface Tip {
  x: number;
  y: number;
  content: ReactNode;
}

/**
 * One tooltip per chart. Marks call show() on pointer move and on keyboard focus,
 * so hover and focus reveal the same details. Content is React nodes, so API
 * strings are always rendered as text, never as HTML.
 */
export function useTooltip() {
  const ref = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<Tip | null>(null);

  const show = useCallback((e: PointerEvent<Element> | FocusEvent<Element>, content: ReactNode) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box) return;
    let x: number;
    let y: number;
    if ("clientX" in e) {
      x = e.clientX - box.left;
      y = e.clientY - box.top;
    } else {
      const r = e.currentTarget.getBoundingClientRect();
      x = r.left + r.width / 2 - box.left;
      y = r.top - box.top;
    }
    // keep the tooltip inside the chart so it never causes horizontal page scroll
    const half = Math.min(120, box.width / 2);
    setTip({ x: Math.min(Math.max(x, half), box.width - half), y, content });
  }, []);

  const hide = useCallback(() => setTip(null), []);

  /** Spread onto a mark: focusable, and hover or keyboard focus shows the same tooltip. */
  const markProps = (content: ReactNode) => ({
    tabIndex: 0,
    onPointerMove: (e: PointerEvent<Element>) => show(e, content),
    onPointerLeave: hide,
    onFocus: (e: FocusEvent<Element>) => show(e, content),
    onBlur: hide,
  });
  return { ref, tip, show, hide, markProps };
}

export function Tooltip({ tip }: { tip: Tip | null }) {
  if (!tip) return null;
  return (
    <div className="tooltip" role="status" style={{ left: tip.x, top: tip.y }}>{tip.content}</div>
  );
}

/** Tooltip row: value first (strong), series name second, keyed by a short line. */
export function TipRow({ color, value, label }: { color?: string; value: ReactNode; label: ReactNode }) {
  return (
    <div className="tip-row">
      {color && <span className="tip-key" style={{ background: color }} />}
      <strong>{value}</strong>
      <span className="muted">{label}</span>
    </div>
  );
}

export interface LegendItem {
  label: string;
  color: string;
  shape?: "rect" | "line" | "hollow";
  opacity?: number;
}

export function Legend({ items }: { items: LegendItem[] }) {
  return (
    <div className="legend">
      {items.map((it) => (
        <span key={it.label}>
          <svg width="14" height="10" aria-hidden="true">
            {it.shape === "line" ? (
              <line x1="0" y1="5" x2="14" y2="5" stroke={it.color} strokeWidth="2" />
            ) : it.shape === "hollow" ? (
              <circle cx="7" cy="5" r="4" fill="none" stroke={it.color} strokeWidth="1.5" />
            ) : (
              <rect x="2" y="0" width="10" height="10" rx="2" style={{ fill: tint(it.color, it.opacity) }} />
            )}
          </svg>
          {it.label}
        </span>
      ))}
    </div>
  );
}
