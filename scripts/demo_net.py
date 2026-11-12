"""Start the whole network for a live demo and show a readable feed.

Terminal 1 (leave it on screen):
    python -m scripts.demo_net --run-id demo

Terminal 2 (send traffic):
    python -m src.host --name host1 --probe host2 --count 3 --run-id demo

The feed shows every device's events as they happen, host1's included,
because host1 writes to the same run folder. Ctrl-C stops the network.
Afterwards, `python -m scripts.trace logs/demo 2` replays probe 2 hop by hop.
"""

import argparse
import json
import os
import shutil
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from scripts.launch import LaunchError, Network, apply_link_settings, router_drop_rule_arg
from scripts.trace import describe, is_router
from src.config import ConfigError, load_config
from src.run_log import DEFAULT_LOG_DIR, run_id_arg
from src.shutdown import stop_on_signals

# Hidden unless --all: the "forwards" line already says where a packet
# went, and a host's "sends" line already shows what it answered.
QUIET_EVENTS = {
    ("router", "received"),
    ("router", "send_scheduled"),
    ("router", "sent"),
    ("host", "received"),
}

COLORS = {"reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
          "red": "\033[31m", "green": "\033[32m"}


class LogFollower:
    """Read new, complete JSON lines from every .jsonl file in a folder."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.offsets = {}
        self.partial = {}

    def poll(self):
        records = []
        for path in sorted(self.folder.glob("*.jsonl")):
            with open(path, "rb") as file:
                file.seek(self.offsets.get(path, 0))
                data = file.read()
            self.offsets[path] = self.offsets.get(path, 0) + len(data)

            lines = (self.partial.pop(path, b"") + data).split(b"\n")
            self.partial[path] = lines.pop()  # unfinished last line, if any
            for line in lines:
                if line.strip():
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return sorted(records, key=lambda r: r.get("timestamp", 0))


def feed_line(record, show_all=False, color=False):
    """The feed's line for one record, or None if it is hidden."""
    device = record.get("device", "?")
    role = "router" if is_router(device) else "host"
    event = record.get("event", "")
    if not show_all and (role, event) in QUIET_EVENTS:
        return None

    clock = datetime.fromtimestamp(record.get("timestamp", 0)).strftime("%H:%M:%S.%f")[:-3]
    text = describe(record)
    tag = f"  [{record['message_id']}]" if record.get("message_id") else ""

    if not color:
        return f"{clock}  {device:<8} {text}{tag}"

    c = COLORS
    failed = event == "transfer_done" and record.get("success") is False
    if (event.startswith("drop") or failed
            or event in ("probe_timeout", "ack_timeout", "transfer_failed")):
        text = f"{c['red']}{text}{c['reset']}"
    elif event in ("echo_received", "ack_received", "transfer_done"):
        text = f"{c['green']}{text}{c['reset']}"
    return (f"{c['dim']}{clock}{c['reset']}  {c['bold']}{device:<8}{c['reset']} "
            f"{text}{c['dim']}{tag}{c['reset']}")


def follow(follower, stop, out=sys.stdout, show_all=False, color=False, interval=0.1):
    """Print the feed until stop is set, then print what is left.

    Sleeps with time.sleep rather than Event.wait: Ctrl-C interrupts a
    sleep cleanly, but can land inside Event.wait's lock handling.
    """
    while True:
        finished = stop.is_set()
        for record in follower.poll():
            line = feed_line(record, show_all, color)
            if line:
                print(line, file=out, flush=True)
        if finished:
            return
        time.sleep(interval)


def use_color(stream):
    return stream.isatty() and "NO_COLOR" not in os.environ


def build_parser():
    parser = argparse.ArgumentParser(
        description="Start the 4 routers and host2, then show every event live."
    )
    parser.add_argument("--config", default="config/topology_upper.json")
    parser.add_argument("--run-id", type=run_id_arg, default="demo")
    parser.add_argument("--log-dir", default=DEFAULT_LOG_DIR)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--replace", action="store_true",
                        help="delete an existing LOG_DIR/RUN_ID folder first")
    parser.add_argument("--link", help="link to change, e.g. routerB-routerD")
    parser.add_argument("--loss", type=float, help="loss rate for --link (0 to 1)")
    parser.add_argument("--delay-ms", type=float, help="one-way delay for --link")
    parser.add_argument(
        "--drop-rule", type=router_drop_rule_arg, action="append", default=[],
        metavar="ROUTER:RULE", help="e.g. routerB:type=PROBE,skip=1; repeatable",
    )
    parser.add_argument("--all", action="store_true",
                        help="also show every receive, queue and send step")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    run_dir = Path(args.log_dir) / args.run_id

    if (args.loss is not None or args.delay_ms is not None) and args.link is None:
        print("error: --loss and --delay-ms need --link", file=sys.stderr)
        return 2

    if run_dir.exists():
        if not args.replace:
            print(f"error: {run_dir} already exists; pick another --run-id "
                  f"or add --replace to delete it", file=sys.stderr)
            return 2
        shutil.rmtree(run_dir)

    try:
        config = load_config(args.config)
        config_path = Path(args.config)
        if args.link is not None:
            config = apply_link_settings(config, args.link, args.loss, args.delay_ms)
            run_dir.mkdir(parents=True)
            config_path = run_dir / "config.json"
            config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

        network = Network(config_path, args.run_id, args.log_dir, args.seed,
                          drop_rules=args.drop_rule)
        network.start()
    except (ConfigError, LaunchError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("If a port is in use, stop old processes with: "
              "pkill -f 'src.(router|host)'", file=sys.stderr)
        return 2

    print(f"Network is up: run '{args.run_id}', config {args.config}. "
          f"Logs: {run_dir}/")
    log_dir_arg = "" if args.log_dir == DEFAULT_LOG_DIR else f" --log-dir {args.log_dir}"
    print("Send probes from another terminal, for example:")
    print(f"  python -m src.host --name host1 --probe host2 --count 3 "
          f"--run-id {args.run_id}{log_dir_arg}")
    print("Press Ctrl-C here to stop the network.\n", flush=True)

    follower = LogFollower(run_dir)
    color = use_color(sys.stdout)
    stop = threading.Event()
    try:
        follow(follower, stop, show_all=args.all, color=color)
    except KeyboardInterrupt:
        pass
    finally:
        # A second Ctrl-C must not cut the shutdown short.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        network.stop()
        stop.set()
        follow(follower, stop, show_all=args.all, color=color)  # last few lines
        print("\nNetwork stopped. Trace a probe with: "
              f"python -m scripts.trace {run_dir} 1")
    return 0


if __name__ == "__main__":
    stop_on_signals()
    sys.exit(main())
