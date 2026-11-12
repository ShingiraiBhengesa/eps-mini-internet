"""Run the baseline experiment matrix (assignment section 6.2).

    python -m scripts.run_baseline

Defaults: 2 one-way delays (20 ms, 80 ms) x 2 loss rates (0%, 5%) on the
routerB-routerD link of the upper path, 3 runs per condition, 20 probes
per run. Every run gets its own seed (1, 2, 3, ...), recorded in its
run.json and in each router's "started" log line.

Held fixed across all runs: topology and path (config/topology_upper.json,
so probes and echoes both cross routerB-routerD), message size (10-byte
payloads "probe-0001" ...), probe interval and timeout. The 1 s timeout is
well above the slowest expected round trip (2 x 80 ms plus processing).

Results go to results/baseline/<run-id>/, then runs.csv and
conditions.csv are written next to them.
"""

import argparse
import sys
from pathlib import Path

from scripts import launch, metrics


def condition_label(delay_ms, loss):
    return f"delay {delay_ms:g} ms, loss {loss:.0%}"


def run_id_for(delay_ms, loss, seed):
    return f"baseline-d{delay_ms:g}-l{round(loss * 100):d}-s{seed}"


def plan(delays, losses, runs_per_condition, first_seed):
    """Every (delay, loss, seed) to run, with a distinct seed per run."""
    seed = first_seed
    runs = []
    for delay_ms in delays:
        for loss in losses:
            for _ in range(runs_per_condition):
                runs.append((delay_ms, loss, seed))
                seed += 1
    return runs


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the baseline experiment matrix.")
    parser.add_argument("--out", default="results/baseline")
    parser.add_argument("--config", default="config/topology_upper.json")
    parser.add_argument("--link", default="routerB-routerD")
    parser.add_argument("--delays", type=float, nargs="+", default=[20, 80],
                        metavar="MS", help="one-way delays on --link")
    parser.add_argument("--losses", type=float, nargs="+", default=[0.0, 0.05],
                        metavar="RATE", help="loss rates on --link (0 to 1)")
    parser.add_argument("--runs", type=int, default=3, help="runs per condition")
    parser.add_argument("--first-seed", type=int, default=1)
    parser.add_argument("--count", type=int, default=20, help="probes per run")
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--skip-existing", action="store_true",
                        help="leave runs that already have a folder alone")
    args = parser.parse_args(argv)

    out = Path(args.out)
    todo = plan(args.delays, args.losses, args.runs, args.first_seed)
    print(f"{len(todo)} runs -> {out}")

    for delay_ms, loss, seed in todo:
        run_id = run_id_for(delay_ms, loss, seed)
        if args.skip_existing and (out / run_id).exists():
            print(f"{run_id}: already done, skipping")
            continue

        try:
            _, meta = launch.run_experiment(
                args.config, run_id, seed, out,
                link=args.link, loss=loss, delay_ms=delay_ms,
                count=args.count, interval=args.interval, timeout=args.timeout,
                condition=condition_label(delay_ms, loss),
            )
        except (launch.LaunchError, launch.ConfigError, OSError) as exc:
            print(f"error in {run_id}: {exc}", file=sys.stderr)
            return 2

        print(f"{run_id}: {meta['sender_summary']} ({meta['duration_s']} s)")

    _, conditions = metrics.summarize(out)
    print()
    print(metrics.format_table(conditions))
    return 0


if __name__ == "__main__":
    sys.exit(main())
