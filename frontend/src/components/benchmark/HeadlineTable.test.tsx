import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import fixture from "../../__fixtures__/benchmark_report.json";
import { asReport } from "../../views/BenchmarksView";
import { HeadlineTable } from "./HeadlineTable";

const report = asReport(fixture)!;

describe("HeadlineTable", () => {
  it("accepts the sample report shape", () => {
    expect(report).not.toBeNull();
    expect(asReport({ something: "else" })).toBeNull();
  });

  it("renders one row per strategy with CI, latency, cost and error rate", () => {
    render(<HeadlineTable caption="Strategies" strategies={report.strategies} />);
    const rows = screen.getAllByRole("row").slice(1); // skip the header row
    expect(rows.map((r) => within(r).getByRole("rowheader").textContent)).toEqual(Object.keys(report.strategies));

    const adaptive = within(screen.getByRole("rowheader", { name: "adaptive" }).closest("tr")!);
    const s = report.strategies.adaptive!.summary;
    expect(adaptive.getByText("70.0% (40%–100%)")).toBeInTheDocument(); // routing accuracy, 95% CI
    expect(adaptive.getByText("60.0% (30%–90%)")).toBeInTheDocument(); // task success, 95% CI
    expect(adaptive.getByText(`$${s.cost_usd.per_1k_queries.toFixed(4)}`)).toBeInTheDocument();
    expect(adaptive.getByText("0.0%")).toBeInTheDocument(); // error rate
    expect(screen.getByText(/10 items × 3 seeds/)).toBeInTheDocument();
  });
});
