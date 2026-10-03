import { humanize } from "../format";

// Status is always icon + text label; the status colour only tints the icon.
const STATUS: Record<string, { icon: string; color: string }> = {
  completed: { icon: "✓", color: "var(--good)" },
  success: { icon: "✓", color: "var(--good)" },
  ok: { icon: "✓", color: "var(--good)" },
  routed: { icon: "→", color: "var(--ink-muted)" },
  queued: { icon: "◷", color: "var(--warning)" },
  running: { icon: "◔", color: "var(--warning)" },
  timeout: { icon: "⏱", color: "var(--serious)" },
  failed: { icon: "✗", color: "var(--critical)" },
  failure: { icon: "✗", color: "var(--critical)" },
  error: { icon: "✗", color: "var(--critical)" },
};

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const s = STATUS[status] ?? { icon: "•", color: "var(--ink-muted)" };
  return (
    <span className="status">
      <span className="icon" style={{ color: s.color }} aria-hidden="true">{s.icon}</span>
      {label ?? humanize(status)}
    </span>
  );
}
