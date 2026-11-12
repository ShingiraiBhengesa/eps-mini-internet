"""Summarize experiment runs from their log files.

    python -m scripts.metrics results/baseline

Finds every run folder (one with a run.json) under the given folder,
prints a per-condition table, and writes two CSV files there:
    runs.csv        one row per run
    conditions.csv  runs grouped by their condition label

Failed and partial runs are kept: a run with timeouts simply has a lower
success fraction, and a run whose sender never finished is flagged.
"""

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

RUN_FIELDS = [
    "run_id", "condition", "seed", "link", "loss", "delay_ms",
    "probes", "answered", "timeouts", "success_fraction",
    "rtt_mean_ms", "rtt_sd_ms", "rtt_min_ms", "rtt_median_ms", "rtt_max_ms",
    "unexpected_echoes", "drops_injected", "drops_forced", "drops_other",
    "sender_exit_code", "complete",
]

CONDITION_FIELDS = [
    "condition", "link", "loss", "delay_ms", "runs", "seeds",
    "probes", "answered", "timeouts", "success_fraction", "success_sd",
    "rtt_mean_ms", "rtt_sd_ms", "rtt_median_ms", "rtt_min_ms", "rtt_max_ms",
    "drops_injected", "incomplete_runs",
]


def read_jsonl(path):
    """Read a JSON-lines log, skipping a torn last line from a killed process."""
    records = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return records

    for line in lines:
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def find_runs(root):
    return sorted(path.parent for path in Path(root).rglob("run.json"))


def sd(values):
    return statistics.stdev(values) if len(values) >= 2 else 0.0


def rounded(value, places=3):
    return None if value is None else round(value, places)


def run_metrics(run_dir):
    """Metrics for one run folder, plus its raw RTT samples under 'rtts'."""
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    sender = meta.get("sender", "host1")
    records = read_jsonl(run_dir / f"{sender}.jsonl")

    probes = sum(
        1 for r in records
        if r.get("event") == "sent" and r.get("type") == "PROBE"
    )
    rtts = [
        float(r["rtt_ms"]) for r in records
        if r.get("event") == "echo_received"
    ]
    timeouts = sum(1 for r in records if r.get("event") == "probe_timeout")
    unexpected = sum(
        1 for r in records
        if r.get("event") in ("dropped_unexpected_echo", "echo_payload_mismatch")
    )

    drops = {"drop_injected": 0, "drop_forced": 0, "drop": 0}
    for log in run_dir.glob("router*.jsonl"):
        for r in read_jsonl(log):
            if r.get("event") in drops:
                drops[r["event"]] += 1

    answered = len(rtts)
    complete = (
        "launch_error" not in meta
        and probes == meta.get("count")
        and answered + timeouts == probes
    )

    return {
        "run_id": meta["run_id"],
        "condition": meta.get("condition") or meta["run_id"],
        "seed": meta.get("seed"),
        "link": meta.get("link"),
        "loss": meta.get("loss"),
        "delay_ms": meta.get("delay_ms"),
        "probes": probes,
        "answered": answered,
        "timeouts": timeouts,
        "success_fraction": rounded(answered / probes if probes else None, 4),
        "rtt_mean_ms": rounded(statistics.fmean(rtts)) if rtts else None,
        "rtt_sd_ms": rounded(sd(rtts)) if rtts else None,
        "rtt_min_ms": rounded(min(rtts)) if rtts else None,
        "rtt_median_ms": rounded(statistics.median(rtts)) if rtts else None,
        "rtt_max_ms": rounded(max(rtts)) if rtts else None,
        "unexpected_echoes": unexpected,
        "drops_injected": drops["drop_injected"],
        "drops_forced": drops["drop_forced"],
        "drops_other": drops["drop"],
        "sender_exit_code": meta.get("sender_exit_code"),
        "complete": complete,
        "rtts": rtts,
    }


def condition_metrics(runs):
    """Group runs by condition label and pool their probes and RTTs."""
    groups = {}
    for run in runs:
        groups.setdefault(run["condition"], []).append(run)

    rows = []
    for label, group in groups.items():
        probes = sum(r["probes"] for r in group)
        answered = sum(r["answered"] for r in group)
        rtts = [rtt for r in group for rtt in r["rtts"]]
        fractions = [r["success_fraction"] for r in group if r["success_fraction"] is not None]
        first = group[0]

        rows.append({
            "condition": label,
            "link": first["link"],
            "loss": first["loss"],
            "delay_ms": first["delay_ms"],
            "runs": len(group),
            "seeds": " ".join(str(r["seed"]) for r in group),
            "probes": probes,
            "answered": answered,
            "timeouts": sum(r["timeouts"] for r in group),
            "success_fraction": rounded(answered / probes if probes else None, 4),
            "success_sd": rounded(sd(fractions), 4),
            "rtt_mean_ms": rounded(statistics.fmean(rtts)) if rtts else None,
            "rtt_sd_ms": rounded(sd(rtts)) if rtts else None,
            "rtt_median_ms": rounded(statistics.median(rtts)) if rtts else None,
            "rtt_min_ms": rounded(min(rtts)) if rtts else None,
            "rtt_max_ms": rounded(max(rtts)) if rtts else None,
            "drops_injected": sum(r["drops_injected"] for r in group),
            "incomplete_runs": sum(1 for r in group if not r["complete"]),
        })

    def order(row):
        return (
            row["delay_ms"] if row["delay_ms"] is not None else -1,
            row["loss"] if row["loss"] is not None else -1,
            row["condition"],
        )

    return sorted(rows, key=order)


def write_csv(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def format_table(rows):
    header = (
        f"{'condition':<24} {'runs':>4} {'probes':>6} {'success':>8} "
        f"{'± sd':>6} {'RTT ms':>8} {'± sd':>7} {'median':>7} {'timeouts':>8}"
    )
    lines = [header, "-" * len(header)]

    for row in rows:
        success = "-" if row["success_fraction"] is None else f"{row['success_fraction']:.1%}"
        rtt = "-" if row["rtt_mean_ms"] is None else f"{row['rtt_mean_ms']:.1f}"
        rtt_sd = "-" if row["rtt_sd_ms"] is None else f"{row['rtt_sd_ms']:.1f}"
        median = "-" if row["rtt_median_ms"] is None else f"{row['rtt_median_ms']:.1f}"
        flag = f"  ({row['incomplete_runs']} incomplete)" if row["incomplete_runs"] else ""
        lines.append(
            f"{row['condition']:<24} {row['runs']:>4} {row['probes']:>6} "
            f"{success:>8} {row['success_sd']:>6.1%} {rtt:>8} {rtt_sd:>7} "
            f"{median:>7} {row['timeouts']:>8}{flag}"
        )
    return "\n".join(lines)


def summarize(root):
    """Compute metrics for every run under root and write the CSV files."""
    root = Path(root)
    runs = [run_metrics(run_dir) for run_dir in find_runs(root)]
    conditions = condition_metrics(runs)

    write_csv(root / "runs.csv", RUN_FIELDS, runs)
    write_csv(root / "conditions.csv", CONDITION_FIELDS, conditions)
    return runs, conditions


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("root", help="folder holding run folders, e.g. results/baseline")
    args = parser.parse_args(argv)

    runs, conditions = summarize(args.root)
    if not runs:
        print(f"error: no run folders (with run.json) under {args.root}", file=sys.stderr)
        return 2

    print(format_table(conditions))
    print(f"\n{len(runs)} runs. Wrote {Path(args.root) / 'runs.csv'} "
          f"and {Path(args.root) / 'conditions.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
