import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { TraceSpan } from "../types";
import { layoutSpans, Waterfall } from "./Waterfall";

const span = (span_id: string, parent_span_id: string | null, start_offset_ms: number, duration_ms: number, status = "ok"): TraceSpan => ({
  span_id,
  parent_span_id,
  name: `op-${span_id}`,
  service: "adaptiveroute-api",
  start_offset_ms,
  duration_ms,
  status,
  attributes: { "ar.agent": "sql" },
});

// Deliberately out of order: layout must not depend on the API's ordering.
const spans = [
  span("d", "a", 60, 30),
  span("c", "b", 20, 10, "error"),
  span("a", null, 0, 100),
  span("b", "a", 10, 40),
  span("e", "missing-parent", 90, 10),
];

describe("layoutSpans", () => {
  const rows = layoutSpans(spans, 100);

  it("orders spans depth-first with children under their parent, siblings by start", () => {
    expect(rows.map((r) => r.span.span_id)).toEqual(["a", "b", "c", "d", "e"]);
  });

  it("derives depth from parent links; unknown parents become roots", () => {
    expect(rows.map((r) => r.depth)).toEqual([0, 1, 2, 1, 0]);
  });

  it("positions bars by offset and duration on one shared axis", () => {
    const b = rows.find((r) => r.span.span_id === "b")!;
    expect(b.left).toBeCloseTo(10);
    expect(b.width).toBeCloseTo(40);
    const e = rows.find((r) => r.span.span_id === "e")!;
    expect(e.left + e.width).toBeCloseTo(100);
  });

  it("extends the axis when a span ends after the reported duration", () => {
    const [root] = layoutSpans([span("a", null, 0, 200)], 100);
    expect(root!.width).toBeCloseTo(100);
  });
});

describe("Waterfall", () => {
  it("marks error spans with an icon (not colour alone) and shows attributes on click", () => {
    render(<Waterfall trace={{ trace_id: "t", duration_ms: 100, spans, ui_url: null }} />);
    expect(screen.getByLabelText("error")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /op-c/ }));
    expect(screen.getByText("ar.agent")).toBeInTheDocument();
  });
});
