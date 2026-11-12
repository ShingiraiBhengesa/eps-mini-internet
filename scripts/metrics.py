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
    if meta.get("kind") == "transfer":
        return transfer_metrics(run_dir, meta)
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


# ---------------------------------------------------------------- Track B transfers

TRANSFER_RUN_FIELDS = [
    "run_id", "condition", "seed", "mode", "link", "loss", "delay_ms",
    "messages", "delivered", "delivered_fraction", "full_sequence", "in_order",
    "duplicates_at_receiver", "data_sent", "retransmissions",
    "sender_success", "failed_at", "sender_elapsed_s", "delivery_time_s",
    "data_drops", "ack_drops", "receiver_stop", "sender_exit_code", "complete",
]

TRANSFER_CONDITION_FIELDS = [
    "condition", "mode", "link", "loss", "delay_ms", "runs", "seeds",
    "messages", "delivered", "delivered_fraction", "delivered_sd",
    "full_sequence_runs", "retransmissions", "retransmissions_per_run",
    "duplicates_at_receiver", "data_sent",
    "delivery_time_mean_s", "delivery_time_sd_s",
    "sender_elapsed_mean_s", "sender_elapsed_sd_s", "incomplete_runs",
]


def last_event(records, event):
    return next((r for r in reversed(records) if r.get("event") == event), None)


def transfer_metrics(run_dir, meta):
    """Metrics for one Track B run, from the sender's and receiver's logs.

    delivery_time_s runs from the sender starting the transfer to the last
    new message delivered at the receiver, so it means the same thing for
    stop-and-wait and best-effort.
    """
    sender_log = read_jsonl(run_dir / f"{meta['sender']}.jsonl")
    receiver_log = read_jsonl(run_dir / f"{meta['receiver']}.jsonl")
    count = meta["count"]

    started = last_event(sender_log, "transfer_started")
    done = last_event(sender_log, "transfer_done")
    receiver_done = last_event(receiver_log, "receiver_done")
    deliveries = [r for r in receiver_log if r.get("event") == "delivered"]
    order = [r["seq"] for r in deliveries]
    unique = sorted(set(order))

    drops = {"DATA": 0, "ACK": 0}
    for log in run_dir.glob("router*.jsonl"):
        for r in read_jsonl(log):
            if r.get("event") in ("drop_injected", "drop_forced", "drop") and r.get("type") in drops:
                drops[r["type"]] += 1

    delivery_time = None
    if started and deliveries:
        delivery_time = round(deliveries[-1]["timestamp"] - started["timestamp"], 3)

    return {
        "kind": "transfer",
        "run_id": meta["run_id"],
        "condition": meta.get("condition") or meta["run_id"],
        "seed": meta.get("seed"),
        "mode": meta.get("mode"),
        "link": meta.get("link"),
        "loss": meta.get("loss"),
        "delay_ms": meta.get("delay_ms"),
        "messages": count,
        "delivered": len(unique),
        "delivered_fraction": round(len(unique) / count, 4),
        "full_sequence": unique == list(range(1, count + 1)),
        "in_order": order == sorted(order),
        "duplicates_at_receiver": sum(1 for r in receiver_log if r.get("event") == "duplicate"),
        "data_sent": sum(1 for r in sender_log
                         if r.get("event") == "sent" and r.get("type") == "DATA"),
        "retransmissions": done.get("retransmissions") if done else None,
        "sender_success": done.get("success") if done else None,
        "failed_at": done.get("failed_at") if done else None,
        "sender_elapsed_s": done.get("elapsed_s") if done else None,
        "delivery_time_s": delivery_time,
        "data_drops": drops["DATA"],
        "ack_drops": drops["ACK"],
        "receiver_stop": receiver_done.get("reason") if receiver_done else None,
        "sender_exit_code": meta.get("sender_exit_code"),
        "complete": done is not None and "launch_error" not in meta,
    }


def mean_sd(values):
    values = [v for v in values if v is not None]
    if not values:
        return None, None
    return round(statistics.fmean(values), 3), round(sd(values), 3)


def transfer_condition_metrics(runs):
    groups = {}
    for run in runs:
        groups.setdefault(run["condition"], []).append(run)

    rows = []
    for label, group in groups.items():
        first = group[0]
        messages = sum(r["messages"] for r in group)
        delivered = sum(r["delivered"] for r in group)
        retransmissions = sum(r["retransmissions"] or 0 for r in group)
        delivery_mean, delivery_sd = mean_sd([r["delivery_time_s"] for r in group])
        elapsed_mean, elapsed_sd = mean_sd([r["sender_elapsed_s"] for r in group])
        rows.append({
            "condition": label,
            "mode": first["mode"],
            "link": first["link"],
            "loss": first["loss"],
            "delay_ms": first["delay_ms"],
            "runs": len(group),
            "seeds": " ".join(str(r["seed"]) for r in group),
            "messages": messages,
            "delivered": delivered,
            "delivered_fraction": round(delivered / messages, 4) if messages else None,
            "delivered_sd": round(sd([r["delivered_fraction"] for r in group]), 4),
            "full_sequence_runs": sum(1 for r in group if r["full_sequence"]),
            "retransmissions": retransmissions,
            "retransmissions_per_run": round(retransmissions / len(group), 2),
            "duplicates_at_receiver": sum(r["duplicates_at_receiver"] for r in group),
            "data_sent": sum(r["data_sent"] for r in group),
            "delivery_time_mean_s": delivery_mean,
            "delivery_time_sd_s": delivery_sd,
            "sender_elapsed_mean_s": elapsed_mean,
            "sender_elapsed_sd_s": elapsed_sd,
            "incomplete_runs": sum(1 for r in group if not r["complete"]),
        })

    return sorted(rows, key=lambda r: (r["loss"] if r["loss"] is not None else -1,
                                       r["mode"] or "", r["condition"]))


def format_transfer_table(rows):
    header = (f"{'condition':<28} {'runs':>4} {'delivered':>10} {'full seq':>8} "
              f"{'resends/run':>11} {'dupes':>5} {'time s':>7} {'± sd':>6}")
    lines = [header, "-" * len(header)]
    for row in rows:
        time_s = "-" if row["delivery_time_mean_s"] is None else f"{row['delivery_time_mean_s']:.2f}"
        time_sd = "-" if row["delivery_time_sd_s"] is None else f"{row['delivery_time_sd_s']:.2f}"
        flag = f"  ({row['incomplete_runs']} incomplete)" if row["incomplete_runs"] else ""
        lines.append(
            f"{row['condition']:<28} {row['runs']:>4} {row['delivered_fraction']:>10.1%} "
            f"{row['full_sequence_runs']:>4}/{row['runs']:<3} {row['retransmissions_per_run']:>11.1f} "
            f"{row['duplicates_at_receiver']:>5} {time_s:>7} {time_sd:>6}{flag}"
        )
    return "\n".join(lines)


def summarize(root):
    """Compute metrics for every run under root and write the CSV files.

    A folder holds either probe runs or Track B transfer runs, and the CSV
    columns follow whichever kind it holds.
    """
    root = Path(root)
    runs = [run_metrics(run_dir) for run_dir in find_runs(root)]

    if runs and all(run.get("kind") == "transfer" for run in runs):
        conditions = transfer_condition_metrics(runs)
        write_csv(root / "runs.csv", TRANSFER_RUN_FIELDS, runs)
        write_csv(root / "conditions.csv", TRANSFER_CONDITION_FIELDS, conditions)
        return runs, conditions

    runs = [run for run in runs if run.get("kind") != "transfer"]
    conditions = condition_metrics(runs)
    write_csv(root / "runs.csv", RUN_FIELDS, runs)
    write_csv(root / "conditions.csv", CONDITION_FIELDS, conditions)
    return runs, conditions


def table_for(conditions):
    if conditions and "full_sequence_runs" in conditions[0]:
        return format_transfer_table(conditions)
    return format_table(conditions)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("root", help="folder holding run folders, e.g. results/baseline")
    args = parser.parse_args(argv)

    runs, conditions = summarize(args.root)
    if not runs:
        print(f"error: no run folders (with run.json) under {args.root}", file=sys.stderr)
        return 2

    print(table_for(conditions))
    print(f"\n{len(runs)} runs. Wrote {Path(args.root) / 'runs.csv'} "
          f"and {Path(args.root) / 'conditions.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
