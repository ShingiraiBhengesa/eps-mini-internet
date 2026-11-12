"""Run one experiment: start the network, run the sender, stop everything.

Example (20 probes over the upper path with 5% loss and 20 ms delay on
the routerB-routerD link):

    python -m scripts.launch --config config/topology_upper.json \\
        --run-id demo-1 --seed 1 --out results/manual \\
        --link routerB-routerD --loss 0.05 --delay-ms 20 \\
        --count 20 --interval 0.2 --timeout 1.0

Everything about the run lands in OUT/RUN_ID/:
    config.json      the exact topology used, with the link changes applied
    run.json         seeds, parameters, timings and the sender's exit code
    <device>.jsonl   one log file per router and host
"""

import argparse
import json
import platform
import signal
import subprocess
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from src.conditions import DropRule
from src.config import ConfigError, get_link, load_config, validate_config
from src.run_log import log_path, run_id_arg

REPO_ROOT = Path(__file__).resolve().parent.parent
ROUTERS = ("routerA", "routerB", "routerC", "routerD")


class LaunchError(RuntimeError):
    """The network could not be started or a run could not be set up."""


def router_seed(seed, index):
    """Each router gets its own seed, derived from the run seed.

    Giving every router the same seed would make their random loss
    decisions follow the same sequence, which correlates drops.
    """
    return seed * 10 + index


def apply_link_settings(config, link, loss=None, delay_ms=None):
    """Return a copy of config with one link's loss and/or delay changed."""
    a, sep, b = link.partition("-")
    if not sep or not a or not b:
        raise ConfigError(f"link must look like routerB-routerD, got {link!r}")

    config = deepcopy(config)
    entry = get_link(config, a, b)
    if loss is not None:
        entry["loss"] = loss
    if delay_ms is not None:
        entry["delay_ms"] = delay_ms

    validate_config(config)
    return config


def router_drop_rule_arg(text):
    """'routerB:type=PROBE,skip=1' -> ('routerB', 'type=PROBE,skip=1')."""
    router, sep, rule = text.partition(":")
    if not sep or router not in ROUTERS:
        raise argparse.ArgumentTypeError(
            "drop rule must look like ROUTER:RULE, e.g. routerB:type=PROBE,skip=1"
        )
    try:
        DropRule.parse(rule)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    return router, rule


def code_version():
    """Short commit ID of the code that ran, with '-dirty' for local edits."""
    try:
        result = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def has_started(path):
    try:
        return '"event":"started"' in path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False


class Network:
    """The four routers and the replying host, each in its own process."""

    def __init__(
        self, config_path, run_id, log_dir, seed,
        responder="host2", drop_rules=(), python=sys.executable,
    ):
        self.config_path = Path(config_path).resolve()
        self.run_id = run_id
        self.log_dir = Path(log_dir).resolve()
        self.run_dir = self.log_dir / run_id
        self.seed = seed
        self.responder = responder
        self.drop_rules = list(drop_rules)
        self.python = python
        self.procs = {}

    def common_args(self):
        return [
            "--config", str(self.config_path),
            "--run-id", self.run_id,
            "--log-dir", str(self.log_dir),
        ]

    def router_seeds(self):
        return {name: router_seed(self.seed, i) for i, name in enumerate(ROUTERS)}

    def start(self, ready_timeout=5.0):
        self.run_dir.mkdir(parents=True, exist_ok=True)

        for name, seed in self.router_seeds().items():
            command = [
                self.python, "-m", "src.router", "--name", name,
                "--seed", str(seed), *self.common_args(),
            ]
            for router, rule in self.drop_rules:
                if router == name:
                    command += ["--drop-rule", rule]
            self._spawn(name, command)

        self._spawn(self.responder, [
            self.python, "-m", "src.host", "--name", self.responder,
            *self.common_args(),
        ])

        self._wait_ready(ready_timeout)

    def _spawn(self, name, command):
        errors = open(self.run_dir / f"{name}.stderr.txt", "w", encoding="utf-8")
        process = subprocess.Popen(
            command, cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL, stderr=errors,
        )
        self.procs[name] = (process, errors)

    def _wait_ready(self, timeout):
        deadline = time.monotonic() + timeout
        waiting = set(self.procs)

        while waiting:
            for name in sorted(waiting):
                process = self.procs[name][0]
                if process.poll() is not None:
                    raise LaunchError(
                        f"{name} exited with code {process.returncode} before "
                        f"starting; see {self.run_dir / (name + '.stderr.txt')}"
                    )
                if has_started(log_path(self.log_dir, self.run_id, name)):
                    waiting.discard(name)

            if waiting and time.monotonic() > deadline:
                raise LaunchError(
                    "timed out waiting for " + ", ".join(sorted(waiting)) + " to start"
                )
            time.sleep(0.05)

    def stop(self, timeout=3.0):
        for process, _ in self.procs.values():
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)

        deadline = time.monotonic() + timeout
        for name, (process, errors) in self.procs.items():
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            errors.close()

            path = Path(errors.name)
            if path.exists() and path.stat().st_size == 0:
                path.unlink()

        self.procs = {}

    def __enter__(self):
        try:
            self.start()
        except BaseException:
            self.stop()
            raise
        return self

    def __exit__(self, *exc_info):
        self.stop()
        return False


def probe_command(python, config_path, run_id, log_dir, sender, receiver,
                  count, interval, timeout, payload=None):
    command = [
        python, "-m", "src.host", "--name", sender, "--probe", receiver,
        "--count", str(count), "--interval", str(interval),
        "--timeout", str(timeout),
        "--config", str(config_path), "--run-id", run_id,
        "--log-dir", str(log_dir),
    ]
    if payload is not None:
        command += ["--payload", payload]
    return command


def relative_to_repo(path):
    path = Path(path).resolve()
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def run_experiment(
    base_config, run_id, seed, out_dir,
    link=None, loss=None, delay_ms=None,
    count=20, interval=0.2, timeout=1.0, payload=None,
    drop_rules=(), condition=None,
    sender="host1", receiver="host2", python=sys.executable,
):
    """Run one probe experiment and return (run_dir, run metadata)."""
    out_dir = Path(out_dir).resolve()
    run_dir = out_dir / run_id
    if run_dir.exists():
        raise LaunchError(f"{run_dir} already exists; use a new run ID or delete it")

    config = load_config(base_config)
    if link is not None:
        config = apply_link_settings(config, link, loss, delay_ms)

    run_dir.mkdir(parents=True)
    config_path = run_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    network = Network(
        config_path, run_id, out_dir, seed,
        responder=receiver, drop_rules=drop_rules, python=python,
    )
    rules_by_router = {}
    for router, rule in drop_rules:
        rules_by_router.setdefault(router, []).append(rule)

    meta = {
        "run_id": run_id,
        "condition": condition,
        "seed": seed,
        "router_seeds": network.router_seeds(),
        "base_config": relative_to_repo(base_config),
        "link": link,
        "loss": loss,
        "delay_ms": delay_ms,
        "links": config["links"],
        "paths": config["paths"],
        "sender": sender,
        "receiver": receiver,
        "count": count,
        "interval_s": interval,
        "timeout_s": timeout,
        "payload": payload,
        "drop_rules": rules_by_router,
        "code_version": code_version(),
        "python": platform.python_version(),
    }

    started = time.time()
    meta["started_at"] = datetime.fromtimestamp(started, timezone.utc).isoformat()
    command = probe_command(
        python, config_path, run_id, out_dir, sender, receiver,
        count, interval, timeout, payload,
    )

    try:
        with network:
            result = subprocess.run(
                command, cwd=REPO_ROOT,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                timeout=count * (interval + timeout) + 30,
            )
    except (LaunchError, subprocess.TimeoutExpired) as exc:
        # Keep a record of the failed attempt next to its logs.
        meta["launch_error"] = str(exc)
        (run_dir / "run.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        if isinstance(exc, LaunchError):
            raise
        raise LaunchError(f"sender did not finish in time: {exc}") from exc

    finished = time.time()
    meta["finished_at"] = datetime.fromtimestamp(finished, timezone.utc).isoformat()
    meta["duration_s"] = round(finished - started, 3)
    meta["sender_exit_code"] = result.returncode
    meta["sender_summary"] = result.stderr.strip()

    (run_dir / "run.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return run_dir, meta


def build_parser():
    parser = argparse.ArgumentParser(
        description="Start the 4 routers and the replying host, run the "
                    "probing host, then stop everything."
    )
    parser.add_argument("--config", default="config/topology_upper.json")
    parser.add_argument("--run-id", type=run_id_arg, required=True)
    parser.add_argument("--seed", type=int, required=True,
                        help="run seed; router i gets seed*10+i")
    parser.add_argument("--out", default="results/manual",
                        help="parent folder for the run's folder")
    parser.add_argument("--condition", help="label used to group runs in metrics")
    parser.add_argument("--link", help="link to change, e.g. routerB-routerD")
    parser.add_argument("--loss", type=float, help="loss rate for --link (0 to 1)")
    parser.add_argument("--delay-ms", type=float, help="one-way delay for --link")
    parser.add_argument("--sender", default="host1")
    parser.add_argument("--receiver", default="host2")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--payload")
    parser.add_argument(
        "--drop-rule", type=router_drop_rule_arg, action="append", default=[],
        metavar="ROUTER:RULE",
        help="forced drop on one router, e.g. routerB:type=PROBE,skip=1; repeatable",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    if (args.loss is not None or args.delay_ms is not None) and args.link is None:
        print("error: --loss and --delay-ms need --link", file=sys.stderr)
        return 2

    try:
        run_dir, meta = run_experiment(
            args.config, args.run_id, args.seed, args.out,
            link=args.link, loss=args.loss, delay_ms=args.delay_ms,
            count=args.count, interval=args.interval, timeout=args.timeout,
            payload=args.payload, drop_rules=args.drop_rule,
            condition=args.condition,
            sender=args.sender, receiver=args.receiver,
        )
    except (ConfigError, LaunchError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"{meta['run_id']}: {meta['sender_summary'] or 'no summary'} "
          f"({meta['duration_s']} s) -> {relative_to_repo(run_dir)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
