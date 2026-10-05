"""Phase C: write the benchmark report (results.json + report.md + figures)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Reference palette (validated categorical order) + chart chrome.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
INK, INK_2, MUTED, GRID, AXIS = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SURFACE = "#fcfcfb"

MAIN_ORDER = ["round_robin", "embedding", "llm", "adaptive", "adaptive_warm"]


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def _ci(metric: dict[str, Any]) -> str:
    lo, hi = metric["ci95"]
    return f"{_pct(metric['mean'])} [{100 * lo:.1f}, {100 * hi:.1f}]"


def _signed_pp(d: dict[str, float]) -> str:
    sig = "" if d["ci_low"] <= 0 <= d["ci_high"] else " *"
    return f"{100 * d['mean']:+.1f} pp [{100 * d['ci_low']:+.1f}, {100 * d['ci_high']:+.1f}]{sig}"


def _usd(x: float) -> str:
    return f"${x:.4f}"


def render_markdown(report: dict[str, Any], figures: list[str]) -> str:
    prov = report["provenance"]
    strategies = report["strategies"]
    lines: list[str] = []
    w = lines.append

    w(f"# AdaptiveRoute benchmark: `{report['run_id']}`\n")
    w(
        f"{prov['dataset_items']} evaluation items x {len(prov['agents'])} agents, "
        f"{len(prov['seeds'])} seeds ({', '.join(map(str, prov['seeds']))}). "
        f"Agent outcomes collected from **{prov['llm_provider']}** between "
        f"{prov['matrix_collected'][0]} and {prov['matrix_collected'][1]}; "
        f"costs are estimates at list prices as of {prov['prices_as_of']}.\n"
    )
    w("## Headline results\n")
    w(
        "| Strategy | Routing accuracy [95% CI] | Task success [95% CI] | Latency P50 / P95 (s) "
        "| Routing P50 / P95 (ms) | Cost / 1k queries | Error rate | Routing throughput (req/s, 1 worker) |"
    )
    w("|---|---|---|---|---|---|---|---|")
    for name in [n for n in MAIN_ORDER if n in strategies]:
        s = strategies[name]["summary"]
        tput = s["routing_throughput_per_s"]
        tput_str = f"{tput:,.0f}" if tput else "n/a"
        w(
            f"| **{name}** | {_ci(s['routing_accuracy'])} | {_ci(s['task_success'])} "
            f"| {s['total_latency_ms']['p50'] / 1000:.2f} / {s['total_latency_ms']['p95'] / 1000:.2f} "
            f"| {s['routing_latency_ms']['p50']:.1f} / {s['routing_latency_ms']['p95']:.1f} "
            f"| {_usd(s['cost_usd']['per_1k_queries'])} | {_pct(s['error_rate']['mean'])} "
            f"| {tput_str} |"
        )
    w("")
    w(
        "Latency = routing time (measured in-process; for the LLM router, its recorded API "
        "round trip) + agent execution time (measured against the provider during "
        "collection, including failover). Throughput is the sequential routing rate of a "
        "single worker (1 / mean routing time); see `docs/evaluation.md` for the live API "
        "load test.\n"
    )

    w("### Difference vs. the embedding router (paired bootstrap, `*` = CI excludes 0)\n")
    w("| Strategy | Δ routing accuracy | Δ task success | Δ cost / 1k queries |")
    w("|---|---|---|---|")
    for name in [n for n in MAIN_ORDER if n in strategies and n != "embedding"]:
        d = strategies[name]["vs_embedding"]
        w(
            f"| {name} | {_signed_pp(d['routing_accuracy'])} | {_signed_pp(d['task_success'])} "
            f"| {1000 * d['cost_usd']['mean']:+.4f} USD |"
        )
    w("")

    for fig in figures:
        w(f"![{fig}](figures/{fig})\n")

    w("## Reference points (computed directly from the outcome matrix)\n")
    w(
        "| Policy | Routing accuracy | Task success | Mean execution (s) | Cost / 1k queries | Error rate |"
    )
    w("|---|---|---|---|---|---|")
    for name, b in report["baselines"].items():
        w(
            f"| {name} | {_pct(b['routing_accuracy'])} | {_pct(b['task_success'])} "
            f"| {b['mean_execution_ms'] / 1000:.2f} | {_usd(1000 * b['mean_cost_usd'])} "
            f"| {_pct(b['error_rate'])} |"
        )
    w(
        "\n`label_oracle` always picks the labelled agent (perfect routing accuracy); "
        "`oracle` picks the cheapest agent that succeeds on each item (an upper bound on "
        "success no router can exceed); `always_<agent>` sends everything to one agent.\n"
    )

    if report.get("ablations"):
        w("## Adaptive-router ablations (post-hoc; not used to choose the headline config)\n")
        w("| Variant | Routing accuracy | Task success | Cost / 1k queries | Latency P50 (s) |")
        w("|---|---|---|---|---|")
        for name, entry in report["ablations"].items():
            s = entry["summary"]
            w(
                f"| {name} | {_ci(s['routing_accuracy'])} | {_ci(s['task_success'])} "
                f"| {_usd(s['cost_usd']['per_1k_queries'])} | {s['total_latency_ms']['p50'] / 1000:.2f} |"
            )
        w("")

    w("## By domain\n")
    domains = sorted(next(iter(strategies.values()))["by_domain"])
    w("| Strategy | " + " | ".join(f"{d} acc / success" for d in domains) + " |")
    w("|---|" + "---|" * len(domains))
    for name in [n for n in MAIN_ORDER if n in strategies]:
        bd = strategies[name]["by_domain"]
        cells = [
            f"{_pct(bd[d]['routing_accuracy'])} / {_pct(bd[d]['task_success'])}" for d in domains
        ]
        w(f"| {name} | " + " | ".join(cells) + " |")
    w("")
    w("### Items tagged `ambiguous` vs the rest (routing accuracy / task success)\n")
    w("| Strategy | ambiguous | other |")
    w("|---|---|---|")
    for name in [n for n in MAIN_ORDER if n in strategies]:
        amb = strategies[name]["by_ambiguity"]
        row = [
            f"{_pct(amb[k]['routing_accuracy'])} / {_pct(amb[k]['task_success'])} (n={amb[k]['n']})"
            if k in amb
            else "n/a"
            for k in ("ambiguous", "other")
        ]
        w(f"| {name} | {row[0]} | {row[1]} |")
    w("")

    w("## Agent capability matrix (every agent on every item)\n")
    w(
        "| Agent | Model | Success on own domain | Success on all items | P50 latency (s) | Mean cost | Errors | Cells needing provider retries |"
    )
    w("|---|---|---|---|---|---|---|---|")
    for agent, a in report["agent_matrix"].items():
        w(
            f"| {agent} | `{a['model']}` | {_pct(a['own_domain_success'])} | {_pct(a['overall_success'])} "
            f"| {a['p50_latency_ms'] / 1000:.2f} | {_usd(a['mean_cost_usd'])} | {_pct(a['error_rate'])} "
            f"| {a['retried_cells']} |"
        )
    w("")

    w("## Confusion (labelled domain -> chosen agent, share of items)\n")
    for name in ("embedding", "llm", "adaptive"):
        if name not in strategies:
            continue
        conf = strategies[name]["confusion"]
        agents = list(next(iter(conf.values())))
        w(f"**{name}**\n")
        w("| label \\ chosen | " + " | ".join(agents) + " |")
        w("|---|" + "---|" * len(agents))
        for d, row in conf.items():
            w(f"| {d} | " + " | ".join(f"{100 * row[a]:.0f}%" for a in agents) + " |")
        w("")

    w("## Provenance\n")
    w("```json")
    w(json.dumps(prov, indent=2, default=str))
    w("```\n")
    w("## Caveats\n")
    for caveat in report["caveats"]:
        w(f"- {caveat}")
    w("")
    return "\n".join(lines)


def write_figures(report: dict[str, Any], out_dir: Path) -> list[str]:
    """Render PNG figures with matplotlib (optional dependency: `uv sync --group eval`)."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []

    fig_dir = out_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.size": 10,
            "axes.edgecolor": AXIS,
            "axes.labelcolor": INK_2,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 0.6,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
        }
    )
    strategies = report["strategies"]
    names = [n for n in MAIN_ORDER if n in strategies]
    written: list[str] = []

    # 1. Task success vs cost (identity via direct labels, not colour). Points that
    # coincide (e.g. strategies that made identical choices) share one label.
    fig, ax = plt.subplots(figsize=(7, 4.2))
    labels: dict[tuple[float, float], list[str]] = {}
    for name in names:
        s = strategies[name]["summary"]
        x, y = s["cost_usd"]["per_1k_queries"], 100 * s["task_success"]["mean"]
        lo, hi = (100 * v for v in s["task_success"]["ci95"])
        ax.errorbar(
            x, y, yerr=[[y - lo], [hi - y]], fmt="o", color=SERIES[0], ms=8, lw=1.5, capsize=3
        )
        # Points closer than ~1.5% of their cost and 1.5 pp share one label.
        key = next(
            (k for k in labels if abs(k[0] - x) <= 0.015 * max(x, 1e-9) and abs(k[1] - y) <= 1.5),
            (x, y),
        )
        labels.setdefault(key, []).append(name)
    for (x, y), group in labels.items():
        ax.annotate(
            ", ".join(group),
            (x, y),
            xytext=(7, 4),
            textcoords="offset points",
            color=INK,
            fontsize=9,
        )
    refs: dict[tuple[float, float], list[str]] = {}
    for name in ("oracle", "label_oracle"):
        b = report["baselines"].get(name)
        if b:
            x, y = 1000 * b["mean_cost_usd"], 100 * b["task_success"]
            ax.plot(x, y, "o", mfc="none", mec=MUTED, ms=8, mew=1.5)
            refs.setdefault((round(x, 6), round(y, 3)), []).append(name)
    for (x, y), group in refs.items():
        ax.annotate(
            ", ".join(group),
            (x, y),
            xytext=(7, -12),
            textcoords="offset points",
            color=INK_2,
            fontsize=9,
        )
    ax.set_xlabel("Estimated cost per 1,000 queries (USD)")
    ax.set_ylabel("Task success (%)")
    ax.set_title(
        "Task success vs. cost (bars = 95% CI; hollow = matrix reference points)",
        color=INK,
        fontsize=10,
        loc="left",
    )
    fig.tight_layout()
    fig.savefig(fig_dir / "success_vs_cost.png", dpi=150)
    plt.close(fig)
    written.append("success_vs_cost.png")

    # 2. End-to-end latency P50 / P95.
    fig, ax = plt.subplots(figsize=(7, 3.6))
    ys = range(len(names))
    p50 = [strategies[n]["summary"]["total_latency_ms"]["p50"] / 1000 for n in names]
    p95 = [strategies[n]["summary"]["total_latency_ms"]["p95"] / 1000 for n in names]
    h = 0.36
    ax.barh([y - h / 2 for y in ys], p50, height=h - 0.04, color=SERIES[0], label="P50")
    ax.barh([y + h / 2 for y in ys], p95, height=h - 0.04, color=SERIES[1], label="P95")
    for y, a, b in zip(ys, p50, p95, strict=True):
        ax.annotate(
            f"{a:.1f}s",
            (a, y - h / 2),
            xytext=(4, 0),
            textcoords="offset points",
            va="center",
            color=INK_2,
            fontsize=8,
        )
        ax.annotate(
            f"{b:.1f}s",
            (b, y + h / 2),
            xytext=(4, 0),
            textcoords="offset points",
            va="center",
            color=INK_2,
            fontsize=8,
        )
    ax.set_yticks(list(ys), names)
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("End-to-end latency (s): routing + agent execution")
    ax.legend(frameon=False, loc="lower right")
    fig.tight_layout()
    fig.savefig(fig_dir / "latency.png", dpi=150)
    plt.close(fig)
    written.append("latency.png")

    # 3. Learning curve: rolling task success by arrival order for the cold-start
    # routers; the warm-start variant (measured per held-out fold) is a reference level.
    curves = report.get("learning_curves", {})
    shown = [n for n in ("embedding", "adaptive") if n in curves]
    if shown:
        fig, ax = plt.subplots(figsize=(7, 3.6))
        for color, name in zip(SERIES, shown, strict=False):
            pts = curves[name]
            xs = [p["position"] + 1 for p in pts]
            ys2 = [100 * p["rolling_success"] for p in pts]
            ax.plot(xs, ys2, color=color, lw=2, label=f"{name} (rolling 25)")
        if "adaptive_warm" in strategies:
            level = 100 * strategies["adaptive_warm"]["summary"]["task_success"]["mean"]
            ax.axhline(level, color=SERIES[2], lw=1.5, ls="--", label="adaptive_warm (overall)")
        # The lines often coincide, so identity comes from the legend rather than end
        # labels (which would have to be nudged away from their lines to stay legible).
        ax.set_xlabel("Queries seen (arrival order, cold start)")
        ax.set_ylabel("Task success (%)")
        ax.legend(frameon=False, loc="lower right")
        fig.tight_layout()
        fig.savefig(fig_dir / "learning_curve.png", dpi=150)
        plt.close(fig)
        written.append("learning_curve.png")
    return written


def write_report(report: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(report, indent=2, default=str))
    figures = write_figures(report, out_dir)
    md = render_markdown(report, figures)
    (out_dir / "report.md").write_text(md)
    report["report_markdown"] = md
    return out_dir / "report.md"
