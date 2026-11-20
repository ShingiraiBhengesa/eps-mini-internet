
"""Compare Go-Back-N with and without receiver-advertised flow control.

Run from the project root:

    python -m scripts.run_flow_control

Experiments use identical bounded receivers, network paths, seeds,
and loss settings for both sender configurations.
"""

import argparse
import csv
import statistics
import sys
from pathlib import Path

from scripts import launch, metrics


CONFIG = "config/topology_upper.json"
LINK = "routerB-routerD"
DELAY_MS = 20

LOSSES = (0.0, 0.05)
BUFFER_SIZES = (1, 4)


def experiment_plan(losses, buffers, repeats, first_seed):
    """Generate reproducible experiments for both sender configurations."""
    for loss_index, loss in enumerate(losses):
        for buffer_size in buffers:
            for enabled in (False, True):
                for repeat in range(repeats):
                    seed = first_seed + loss_index * repeats + repeat

                    mode = "controlled" if enabled else "uncontrolled"
                    condition = (
                        f"{mode}, buffer {buffer_size}, loss {loss:.0%}"
                    )
                    run_id = (
                        f"flow-{mode}-b{buffer_size}"
                        f"-l{round(loss * 100)}-s{seed}"
                    )

                    yield {
                        "run_id": run_id,
                        "seed": seed,
                        "loss": loss,
                        "buffer_size": buffer_size,
                        "flow_control": enabled,
                        "condition": condition,
                    }


def run_experiments(args):
    """Execute transfers through the four-router network."""
    output = Path(args.out)

    plan = list(
        experiment_plan(
            args.losses,
            args.buffers,
            args.runs,
            args.first_seed,
        )
    )

    # Avoid silently overwriting existing experimental evidence.
    for case in plan:
        if (output / case["run_id"]).exists():
            raise ValueError(
                f"{output / case['run_id']} already exists. "
                "Use a new output folder to repeat the experiments."
            )

    output.mkdir(parents=True, exist_ok=True)

    for index, case in enumerate(plan, start=1):
        print(
            f"[{index}/{len(plan)}] {case['condition']}, "
            f"seed {case['seed']}",
            flush=True,
        )

        run_dir, meta = launch.run_transfer(
            CONFIG,
            case["run_id"],
            case["seed"],
            output,
            mode="gobackn",
            count=args.count,
            rate=args.rate,
            timeout=args.timeout,
            retries=args.retries,
            linger=args.linger,
            window=args.window,
            link=LINK,
            loss=case["loss"],
            delay_ms=DELAY_MS,
            condition=case["condition"],
            flow_control=case["flow_control"],
            bounded_receiver=True,
            buffer_size=case["buffer_size"],
            consume_interval=args.consume_interval,
        )

        result = metrics.run_metrics(run_dir)

        print(
            f"  Delivered: {result['delivered']}/{result['messages']}, "
            f"retransmissions: {result['retransmissions']}, "
            f"success: {result['sender_success']}",
            flush=True,
        )

    return output


def analyze_run(run_dir):
    """Collect flow-control events and standard transfer metrics."""
    run_dir = Path(run_dir)

    meta = metrics.run_metrics(run_dir)

    sender = metrics.read_jsonl(run_dir / "host1.jsonl")
    receiver = metrics.read_jsonl(run_dir / "host2.jsonl")

    done = metrics.last_event(sender, "transfer_done") or {}

    return {
        "run_id": meta["run_id"],
        "condition": meta["condition"],
        "loss": meta["loss"],
        "messages": meta["messages"],
        "delivered": meta["delivered"],
        "complete_sequence": meta["full_sequence"],
        "sender_success": meta["sender_success"],
        "retransmissions": meta["retransmissions"],
        "elapsed_s": meta["sender_elapsed_s"],
        "max_in_flight": meta["max_in_flight"],
        "window_updates": done.get("window_updates", 0),
        "zero_window_events": done.get("zero_window_events", 0),
        "window_probes": done.get("window_probes", 0),
        "buffer_full_events": sum(
            event.get("event") == "receiver_buffer_full"
            for event in receiver
        ),
    }


def write_summary(output):
    """Write detailed results and grouped comparison statistics."""
    runs = [
        analyze_run(path)
        for path in metrics.find_runs(output)
    ]

    if not runs:
        raise ValueError("No completed experiment folders were found")

    # Generate the project's existing Track B reports.
    metrics.summarize(output)

    detail_path = output / "flow_runs.csv"

    with detail_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(runs[0]))
        writer.writeheader()
        writer.writerows(runs)

    groups = {}

    for run in runs:
        groups.setdefault(run["condition"], []).append(run)

    summary = []

    for condition, group in sorted(groups.items()):
        completed = sum(run["complete_sequence"] for run in group)
        delivered = sum(run["delivered"] for run in group)
        messages = sum(run["messages"] for run in group)

        times = [
            run["elapsed_s"]
            for run in group
            if run["elapsed_s"] is not None
        ]

        retransmissions = [
            run["retransmissions"]
            for run in group
            if run["retransmissions"] is not None
        ]

        row = {
            "condition": condition,
            "runs": len(group),
            "delivery_fraction": round(delivered / messages, 4),
            "complete_runs": completed,
            "mean_elapsed_s": (
                round(statistics.mean(times), 3) if times else None
            ),
            "mean_retransmissions": (
                round(statistics.mean(retransmissions), 2)
                if retransmissions else None
            ),
            "max_in_flight": max(
                (run["max_in_flight"] or 0 for run in group),
                default=0,
            ),
            "zero_window_events": sum(
                run["zero_window_events"] for run in group
            ),
            "window_probes": sum(
                run["window_probes"] for run in group
            ),
            "buffer_full_events": sum(
                run["buffer_full_events"] for run in group
            ),
        }

        summary.append(row)

    summary_path = output / "flow_summary.csv"

    with summary_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)

    print("\nFlow-control comparison")
    print("-" * 90)

    for row in summary:
        print(
            f"{row['condition']}: "
            f"{row['complete_runs']}/{row['runs']} complete, "
            f"delivery {row['delivery_fraction']:.1%}, "
            f"average time {row['mean_elapsed_s']} s, "
            f"average retransmissions {row['mean_retransmissions']}, "
            f"buffer-full events {row['buffer_full_events']}"
        )

    print(f"\nDetailed results: {detail_path}")
    print(f"Summary: {summary_path}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare receiver-advertised flow control"
    )

    parser.add_argument(
        "--out",
        default="results/manual/flow-control",
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--count", type=int, default=15)
    parser.add_argument("--window", type=int, default=4)
    parser.add_argument("--rate", type=float, default=50.0)
    parser.add_argument("--timeout", type=float, default=0.5)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--linger", type=float, default=3.0)
    parser.add_argument("--consume-interval", type=float, default=0.15)
    parser.add_argument("--first-seed", type=int, default=401)
    parser.add_argument(
        "--losses",
        type=float,
        nargs="+",
        default=list(LOSSES),
    )
    parser.add_argument(
        "--buffers",
        type=int,
        nargs="+",
        default=list(BUFFER_SIZES),
    )

    args = parser.parse_args(argv)

    if (
        args.runs < 1
        or args.count < 1
        or args.window < 1
        or args.rate <= 0
        or args.timeout <= 0
        or args.retries < 0
        or args.linger < 0
        or args.consume_interval <= 0
        or any(buffer < 1 for buffer in args.buffers)
        or any(not 0 <= loss <= 1 for loss in args.losses)
    ):
        parser.error("invalid experiment parameters")

    try:
        output = run_experiments(args)
        write_summary(output)
    except (ValueError, launch.LaunchError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
