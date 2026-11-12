"""Plot the Track B comparison from runs.csv (written by scripts.metrics).

    python -m scripts.plot_trackb results/trackb/compare

Writes ROOT/trackb.png with two panels that share the same x layout (loss
rate groups, protocol as color):
    left   share of the messages delivered: one dot per run, a bar at the pooled value
    right  time from the first send to the last delivery: one dot per run,
           a larger dot at the mean, labelled with the mean and resends per run

Requires matplotlib (see requirements.txt).
"""

import argparse
import csv
import statistics
import sys
from pathlib import Path

from scripts.plot_results import FONTS, INK, INK_SECONDARY, SERIES, SURFACE, style_axes

MODES = [("besteffort", "best-effort"), ("stopwait", "stop-and-wait")]


def load_runs(root):
    with open(Path(root) / "runs.csv", newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    if not rows or "delivered_fraction" not in rows[0]:
        raise ValueError(f"{root}/runs.csv is not a Track B transfer summary")
    return [{
        "mode": row["mode"],
        "loss": float(row["loss"]),
        "delivered": int(row["delivered"]),
        "messages": int(row["messages"]),
        "fraction": float(row["delivered_fraction"]),
        "time": float(row["delivery_time_s"]) if row["delivery_time_s"] else None,
        "resends": int(row["retransmissions"]) if row["retransmissions"] else 0,
    } for row in rows]


def plot(root, out_name="trackb.png"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import PercentFormatter

    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = FONTS

    runs = load_runs(root)
    losses = sorted({r["loss"] for r in runs})
    offsets = [-0.16, 0.16]
    fig, (left, right) = plt.subplots(1, 2, figsize=(10, 4.2), dpi=200)
    fig.patch.set_facecolor(SURFACE)

    for ax in (left, right):
        style_axes(ax)
        ax.set_xticks(range(len(losses)))
        ax.set_xticklabels([f"{loss:.0%} loss" for loss in losses])
        ax.set_xlim(-0.6, len(losses) - 0.4)
        ax.set_xlabel("Loss rate on routerB–routerD (each direction)", color=INK_SECONDARY,
                      fontsize=9, labelpad=8)

    lowest, top = 1.0, 0.0
    for mi, (mode, _) in enumerate(MODES):
        color = SERIES[mi]
        for li, loss in enumerate(losses):
            group = [r for r in runs if r["mode"] == mode and r["loss"] == loss]
            if not group:
                continue
            x = li + offsets[mi]
            jitter = [(j - (len(group) - 1) / 2) * 0.06 for j in range(len(group))]

            fractions = [r["fraction"] for r in group]
            pooled = sum(r["delivered"] for r in group) / sum(r["messages"] for r in group)
            lowest = min(lowest, *fractions)
            left.hlines(pooled, x - 0.11, x + 0.11, color=color, linewidth=2.5, zorder=3)
            left.scatter([x + j for j in jitter], fractions, s=46, color=color,
                         edgecolor=SURFACE, linewidth=1.5, zorder=4)

            times = [r["time"] for r in group if r["time"] is not None]
            if not times:
                continue
            mean = statistics.fmean(times)
            top = max(top, *times)
            right.scatter([x + j for j in jitter], times, s=22, color=color, alpha=0.45,
                          linewidth=0, zorder=2)
            right.scatter([x], [mean], s=58, color=color, edgecolor=SURFACE,
                          linewidth=2, zorder=4)
            text = f"{mean:.2f} s"
            if mode == "stopwait":
                text += f"\n{sum(r['resends'] for r in group) / len(group):.1f} resends/run"
            right.annotate(text, (x, max(times)), xytext=(0, 8), textcoords="offset points",
                           ha="center", va="bottom", color=INK_SECONDARY, fontsize=8.5)

    left.set_ylim(max(0.0, (int(lowest * 20) - 1) / 20), 1.012)
    left.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    left.set_title("Messages delivered", loc="left", color=INK, fontsize=11,
                   fontweight="bold", pad=18)
    left.text(0, 1.02, "Unique messages that reached host2. Dots: runs. Bar: pooled.",
              transform=left.transAxes, color=INK_SECONDARY, fontsize=8.5)

    right.set_ylim(0, top * 1.3)
    right.set_title("Time to deliver (s)", loc="left", color=INK, fontsize=11,
                    fontweight="bold", pad=18)
    right.text(0, 1.02, "From the first send to the last new delivery. Large dot: mean.",
               transform=right.transAxes, color=INK_SECONDARY, fontsize=8.5)

    handles = [
        Line2D([0], [0], marker="o", linestyle="", markersize=7,
               markerfacecolor=SERIES[i], markeredgecolor=SURFACE, label=name)
        for i, (_, name) in enumerate(MODES)
    ]
    fig.legend(handles=handles, loc="upper right", ncol=len(handles), frameon=False,
               fontsize=9, labelcolor=INK_SECONDARY, bbox_to_anchor=(0.985, 0.995),
               handletextpad=0.3, columnspacing=1.2)

    fig.tight_layout(rect=(0, 0, 1, 0.94), w_pad=3)
    out = Path(root) / out_name
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description="Plot the Track B comparison.")
    parser.add_argument("root", help="folder with runs.csv, e.g. results/trackb/compare")
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
