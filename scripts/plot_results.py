"""Plot baseline results from runs.csv (written by scripts.metrics).

    python -m scripts.plot_results results/baseline

Writes ROOT/baseline.png with two panels that share the same x layout
(delay groups, loss as color):
    left   share of probes answered: one dot per run, a bar at the pooled value
    right  round-trip time: pooled mean with a +/- 1 SD whisker

Requires matplotlib (see requirements.txt).
"""

import argparse
import csv
import statistics
import sys
from pathlib import Path

# Reference palette (validated for light surfaces, 2 series): series take
# slots 1 and 2 in this fixed order; text uses ink tokens, never series colors.
SERIES = ["#2a78d6", "#eb6834"]
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
FONTS = ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]


def load_runs(root):
    path = Path(root) / "runs.csv"
    with open(path, newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))

    runs = []
    for row in rows:
        runs.append({
            "delay_ms": float(row["delay_ms"]),
            "loss": float(row["loss"]),
            "success": float(row["success_fraction"]) if row["success_fraction"] else None,
            "answered": int(row["answered"]),
            "probes": int(row["probes"]),
        })
    return runs


def load_rtts(root):
    """Every answered probe's RTT, keyed by (delay, loss), from the raw logs."""
    from scripts.metrics import find_runs, run_metrics

    samples = {}
    for run_dir in find_runs(root):
        run = run_metrics(run_dir)
        key = (float(run["delay_ms"]), float(run["loss"]))
        samples.setdefault(key, []).extend(run["rtts"])
    return samples


def style_axes(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.spines["bottom"].set_linewidth(1)
    ax.grid(axis="y", color=GRID, linewidth=1)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", colors=INK_MUTED, labelcolor=INK_SECONDARY,
                   length=0, labelsize=9)
    ax.tick_params(axis="x", pad=6)


def plot(root, out_name="baseline.png"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import PercentFormatter

    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = FONTS

    runs = load_runs(root)
    rtts = load_rtts(root)
    delays = sorted({r["delay_ms"] for r in runs})
    losses = sorted({r["loss"] for r in runs})
    if len(losses) > len(SERIES):
        raise ValueError(f"at most {len(SERIES)} loss rates can be plotted")

    offsets = [(i - (len(losses) - 1) / 2) * 0.32 for i in range(len(losses))]
    fig, (left, right) = plt.subplots(1, 2, figsize=(10, 4.2), dpi=200)
    fig.patch.set_facecolor(SURFACE)

    for ax in (left, right):
        style_axes(ax)
        ax.set_xticks(range(len(delays)))
        ax.set_xticklabels([f"{d:g} ms" for d in delays])
        ax.set_xlim(-0.6, len(delays) - 0.4)
        ax.set_xlabel("One-way delay on routerB–routerD", color=INK_SECONDARY,
                      fontsize=9, labelpad=8)

    # Left: share of probes answered.
    lowest = 1.0
    for li, loss in enumerate(losses):
        color = SERIES[li]
        for di, delay in enumerate(delays):
            group = [r for r in runs if r["delay_ms"] == delay and r["loss"] == loss]
            values = [r["success"] for r in group if r["success"] is not None]
            if not values:
                continue
            x = di + offsets[li]
            pooled = sum(r["answered"] for r in group) / sum(r["probes"] for r in group)
            lowest = min(lowest, *values)

            left.hlines(pooled, x - 0.12, x + 0.12, color=color, linewidth=2.5,
                        zorder=3)
            jitter = [(j - (len(values) - 1) / 2) * 0.07 for j in range(len(values))]
            left.scatter([x + j for j in jitter], values, s=46, color=color,
                         edgecolor=SURFACE, linewidth=1.5, zorder=4)

    floor = max(0.0, (int(lowest * 10) - 1) / 10)
    left.set_ylim(floor, 1.03)
    left.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    left.set_title("Probes answered", loc="left", color=INK, fontsize=11,
                   fontweight="bold", pad=18)
    left.text(0, 1.02, "Dots: each run (20 probes). Bar: all runs pooled.",
              transform=left.transAxes, color=INK_SECONDARY, fontsize=8.5)

    # Right: round-trip time, pooled over every answered probe.
    top = 0.0
    for li, loss in enumerate(losses):
        color = SERIES[li]
        for di, delay in enumerate(delays):
            values = rtts.get((delay, loss), [])
            if not values:
                continue
            x = di + offsets[li]
            mean = statistics.fmean(values)
            spread = statistics.stdev(values) if len(values) > 1 else 0.0
            top = max(top, mean + spread, max(values))

            right.vlines(x, mean - spread, mean + spread, color=color,
                         linewidth=2, zorder=3)
            right.scatter([x], [mean], s=58, color=color, edgecolor=SURFACE,
                          linewidth=2, zorder=4)
            right.annotate(f"{mean:.0f} ± {spread:.1f}", (x, mean + spread),
                           xytext=(0, 8), textcoords="offset points",
                           ha="center", va="bottom",
                           color=INK_SECONDARY, fontsize=8.5)

    right.set_ylim(0, top * 1.15)
    right.set_title("Round-trip time (ms)", loc="left", color=INK, fontsize=11,
                    fontweight="bold", pad=18)
    right.text(0, 1.02, "Mean ± 1 SD of all answered probes (whisker hidden when SD < dot).",
               transform=right.transAxes, color=INK_SECONDARY, fontsize=8.5)

    handles = [
        Line2D([0], [0], marker="o", linestyle="", markersize=7,
               markerfacecolor=SERIES[i], markeredgecolor=SURFACE,
               label=f"{loss:.0%} loss")
        for i, loss in enumerate(losses)
    ]
    fig.legend(handles=handles, loc="upper right", ncol=len(handles),
               frameon=False, fontsize=9, labelcolor=INK_SECONDARY,
               bbox_to_anchor=(0.985, 0.995), handletextpad=0.3,
               columnspacing=1.2)

    fig.tight_layout(rect=(0, 0, 1, 0.94), w_pad=3)
    out = Path(root) / out_name
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description="Plot baseline results.")
    parser.add_argument("root", help="folder with runs.csv, e.g. results/baseline")
    args = parser.parse_args(argv)

    try:
        out = plot(args.root)
    except FileNotFoundError:
        print(f"error: {Path(args.root) / 'runs.csv'} not found; "
              f"run python -m scripts.metrics {args.root} first", file=sys.stderr)
        return 2
    except ImportError:
        print("error: matplotlib is not installed; "
              "run python -m pip install -r requirements.txt", file=sys.stderr)
        return 2

    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
