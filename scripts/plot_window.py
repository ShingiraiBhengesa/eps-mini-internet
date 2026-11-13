"""Plot the Go-Back-N window comparison (the Track B enhancement).

    python -m scripts.plot_window results/trackb/window

Reads ROOT/runs.csv (written by scripts.metrics) and, for the reference
lines, results/trackb/compare/conditions.csv. Writes ROOT/window.png:
    left   time to deliver the 50 messages at each window, 0% and 5% loss,
           with best-effort and stop-and-wait as dashed reference lines
    right  messages resent per run at each window under 5% loss

Requires matplotlib (see requirements.txt).
"""

import argparse
import csv
import statistics
import sys
from pathlib import Path

from scripts.plot_results import (
    BASELINE, FONTS, GRID, INK, INK_MUTED, INK_SECONDARY, SERIES, SURFACE, style_axes,
)

COMPARE = Path("results/trackb/compare/conditions.csv")


def load(root):
    with open(Path(root) / "runs.csv", newline="", encoding="utf-8") as file:
        rows = [r for r in csv.DictReader(file) if r.get("mode") == "gobackn"]
    if not rows:
        raise ValueError(f"{root}/runs.csv has no Go-Back-N runs")
    return [{"window": int(r["window"]), "loss": float(r["loss"]),
             "time": float(r["delivery_time_s"]), "resends": int(r["retransmissions"])}
            for r in rows]


def references():
    """Mean time to deliver for best-effort and stop-and-wait, per loss rate."""
    refs = {}
    if COMPARE.exists():
        with open(COMPARE, newline="", encoding="utf-8") as file:
            for r in csv.DictReader(file):
                refs[(r["mode"], float(r["loss"]))] = float(r["delivery_time_mean_s"])
    return refs


def plot(root, out_name="window.png"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = FONTS

    runs = load(root)
    refs = references()
    windows = sorted({r["window"] for r in runs})
    losses = sorted({r["loss"] for r in runs})
    xs = range(len(windows))
    fig, (left, right) = plt.subplots(1, 2, figsize=(10, 4.4), dpi=200,
                                      gridspec_kw={"width_ratios": [1.35, 1]})
    fig.patch.set_facecolor(SURFACE)
    for ax in (left, right):
        style_axes(ax)
        ax.set_xticks(list(xs))
        ax.set_xticklabels([str(w) for w in windows])
        ax.set_xlim(-0.5, len(windows) - 0.5)
        ax.set_xlabel("Go-Back-N window (messages in flight)", color=INK_SECONDARY,
                      fontsize=9, labelpad=8)

    # Left: time to deliver, one line per loss rate.
    top = 0.0
    for li, loss in enumerate(losses):
        color = SERIES[li]
        means = []
        for xi, w in zip(xs, windows):
            times = [r["time"] for r in runs if r["window"] == w and r["loss"] == loss]
            jitter = [(j - (len(times) - 1) / 2) * 0.07 for j in range(len(times))]
            left.scatter([xi + j for j in jitter], times, s=16, color=color, alpha=0.4,
                         linewidth=0, zorder=2)
            means.append(statistics.fmean(times))
            top = max(top, *times)
        left.plot(list(xs), means, color=color, linewidth=2, zorder=3)
        left.scatter(list(xs), means, s=46, color=color, edgecolor=SURFACE, linewidth=1.5, zorder=4)
        left.annotate(f"{means[-1]:.2f} s", (len(windows) - 1, means[-1]), xytext=(14, 9),
                      textcoords="offset points", va="center", fontsize=8.5, color=INK_SECONDARY)
        left.annotate(f"{means[0]:.2f} s", (0, means[0]), xytext=(-8, 0),
                      textcoords="offset points", va="center", ha="right", fontsize=8.5,
                      color=INK_SECONDARY)

    best = [refs[k] for k in refs if k[0] == "besteffort"]
    if best:
        level = statistics.fmean(best)
        left.axhline(level, color=INK_MUTED, linewidth=1, linestyle=(0, (4, 3)), zorder=1)
        left.text(len(windows) - 0.5, level - 0.06, f"best-effort, no reliability: {level:.2f} s",
                  ha="right", va="top", fontsize=8.5, color=INK_SECONDARY)
    sw = [(loss, refs[("stopwait", loss)]) for loss in losses if ("stopwait", loss) in refs]
    if sw:
        note = "stop-and-wait: " + ", ".join(f"{t:.2f} s at {loss:.0%}" for loss, t in sw)
        left.text(0, 1.02, note + " (a window of 1 matches it)", transform=left.transAxes,
                  fontsize=8.5, color=INK_SECONDARY)
        top = max(top, *(t for _, t in sw))
    left.set_ylim(0, top * 1.12)
    left.set_title("Time to deliver 50 messages (s)", loc="left", color=INK, fontsize=11,
                   fontweight="bold", pad=18)

    # Right: resends under loss.
    lossy = max(losses)
    color = SERIES[losses.index(lossy)]
    for xi, w in zip(xs, windows):
        resends = [r["resends"] for r in runs if r["window"] == w and r["loss"] == lossy]
        jitter = [(j - (len(resends) - 1) / 2) * 0.12 for j in range(len(resends))]
        mean = statistics.fmean(resends)
        right.hlines(mean, xi - 0.2, xi + 0.2, color=color, linewidth=2.5, zorder=3)
        right.scatter([xi + j for j in jitter], resends, s=40, color=color,
                      edgecolor=SURFACE, linewidth=1.5, zorder=4)
        right.annotate(f"{mean:.1f}", (xi, max(resends)), xytext=(0, 7),
                       textcoords="offset points", ha="center", fontsize=8.5,
                       color=INK_SECONDARY)
    right.set_ylim(0, max(r["resends"] for r in runs) * 1.18 + 1)
    right.set_title(f"Messages resent per run, {lossy:.0%} loss", loc="left", color=INK,
                    fontsize=11, fontweight="bold", pad=18)
    right.text(0, 1.02, "Dots: runs. Bar and number: mean. No resends at 0% loss.",
               transform=right.transAxes, fontsize=8.5, color=INK_SECONDARY)

    handles = [Line2D([0], [0], color=SERIES[i], linewidth=2, marker="o", markersize=6,
                      markeredgecolor=SURFACE, label=f"{loss:.0%} loss")
               for i, loss in enumerate(losses)]
    fig.legend(handles=handles, loc="upper right", ncol=len(handles), frameon=False,
               fontsize=9, labelcolor=INK_SECONDARY, bbox_to_anchor=(0.985, 0.995),
               handletextpad=0.4, columnspacing=1.2)

    fig.tight_layout(rect=(0, 0, 1, 0.94), w_pad=3)
    out = Path(root) / out_name
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description="Plot the Go-Back-N window comparison.")
    parser.add_argument("root", help="folder with runs.csv, e.g. results/trackb/window")
    args = parser.parse_args(argv)
    try:
        out = plot(args.root)
    except FileNotFoundError:
        print(f"error: {Path(args.root) / 'runs.csv'} not found; "
              f"run python -m scripts.metrics {args.root} first", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ImportError:
        print("error: matplotlib is not installed; "
              "run python -m pip install -r requirements.txt", file=sys.stderr)
        return 2
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
