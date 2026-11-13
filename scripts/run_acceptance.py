"""Core acceptance evidence (assignment section 6.1), checked automatically.

    python -m scripts.run_acceptance

Each scenario runs on the real network: separate router and host processes
talking over UDP. Its logs are kept in results/acceptance/<scenario>/, the
logs are checked against what should happen, and the results are written to
results/acceptance/README.md (with the commands and key traces) and
summary.csv. The script exits with 1 if any check fails.
"""

import argparse
import csv
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

from scripts import launch
from scripts.trace import format_trace, load_records, trace

UPPER = "config/topology_upper.json"
LOWER = "config/topology_lower.json"
LOOP = "config/topology_loop.json"
LINK = "routerB-routerD"
OUT = "results/acceptance"


# ---------------------------------------------------------------- running

class Session:
    """One network run: start the routers (and a replying host), run steps, stop."""

    def __init__(self, out, run_id, config, responder="host2", seed=0,
                 link=None, loss=None, delay_ms=None, drop_rules=()):
        self.run_dir, self.network, self.meta = launch.prepare_run(
            config, run_id, seed, out, link, loss, delay_ms, drop_rules,
            {"responder": responder, "python": sys.executable},
        )
        self.meta.update(kind="acceptance", responder=responder)
        self.config = json.loads((self.run_dir / "config.json").read_text())
        self.steps = []

    def shown(self, command):
        """The command as a person would type it from the repository root."""
        text = " ".join(command[1:])
        text = text.replace(str(self.network.config_path), launch.relative_to_repo(self.network.config_path))
        text = text.replace(str(self.network.log_dir), launch.relative_to_repo(self.network.log_dir))
        return "python " + text

    def probe(self, sender, dst, count=1, interval=0.2, timeout=1.0, hop_limit=None):
        command = launch.probe_command(
            sys.executable, self.network.config_path, self.network.run_id,
            self.network.log_dir, sender, dst, count, interval, timeout, hop_limit=hop_limit,
        )
        result = subprocess.run(command, cwd=launch.REPO_ROOT, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True,
                                timeout=count * (interval + timeout) + 30)
        self.steps.append({"step": "probe", "command": self.shown(command),
                           "exit_code": result.returncode, "summary": result.stderr.strip()})

    def pause(self, seconds):
        """Keep the network running, e.g. so late replies can finish their trip."""
        time.sleep(seconds)
        self.steps.append({"step": "pause", "seconds": seconds})

    def garbage(self, device, data=b"not a packet"):
        device_cfg = self.config["devices"][device]
        endpoint = (device_cfg["udp_host"], device_cfg["udp_port"])
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(data, endpoint)
        time.sleep(0.3)
        self.steps.append({"step": "garbage", "to": f"{device} {endpoint[0]}:{endpoint[1]}",
                           "bytes": data.decode("ascii", "replace")})

    def __enter__(self):
        self.started = time.time()
        self.network.__enter__()
        return self

    def __exit__(self, *exc):
        self.network.__exit__(*exc)
        self.meta["steps"] = self.steps
        self.meta["duration_s"] = round(time.time() - self.started, 3)
        (self.run_dir / "run.json").write_text(json.dumps(self.meta, indent=2) + "\n", encoding="utf-8")
        return False


# ---------------------------------------------------------------- reading logs

def find(records, event, device=None, **match):
    return [r for r in records if r.get("event") == event
            and (device is None or r.get("device") == device)
            and all(r.get(k) == v for k, v in match.items())]


def probes_sent(records, sender):
    return find(records, "sent", sender, type="PROBE")


def outcome(records, sender, mid):
    if find(records, "echo_received", sender, message_id=mid):
        return "answered"
    if find(records, "probe_timeout", sender, message_id=mid):
        return "timed out"
    return "no result"


def drops_for(records, mid):
    return [r for r in records if r.get("message_id") == mid
            and r.get("event") in ("drop", "drop_injected", "drop_forced")]


def path_of(records, mid):
    _, paths = trace(records, mid)
    return paths[0]["devices"] if paths else []


def neighbor_violations(run_dir):
    """Every send must go to a configured neighbor of the sending device."""
    config = json.loads((run_dir / "config.json").read_text())
    bad = []
    for r in load_records(run_dir):
        if r.get("event") in ("sent", "forward") and r.get("next_hop"):
            neighbors = config["devices"][r["device"]]["neighbors"]
            if r["next_hop"] not in neighbors:
                bad.append(f"{r['device']} -> {r['next_hop']}")
    return bad


class Checks:
    def __init__(self):
        self.rows = []

    def add(self, what, passed, detail=""):
        self.rows.append({"check": what, "passed": bool(passed), "detail": detail})

    @property
    def passed(self):
        return all(r["passed"] for r in self.rows)


# ---------------------------------------------------------------- scenarios

def multihop(out, sender, receiver, run_id):
    with Session(out, run_id, UPPER, responder=receiver) as s:
        s.probe(sender, receiver, count=20, interval=0.1, timeout=1.0)
    recs = load_records(s.run_dir)
    sent = probes_sent(recs, sender)
    answered = [p for p in sent if outcome(recs, sender, p["message_id"]) == "answered"]
    c = Checks()
    c.add(f"20 probes from {sender}, 20 matching echoes", len(sent) == 20 and len(answered) == 20,
          f"{len(answered)} of {len(sent)} answered")
    c.add("every echo matched its probe's ID, sender and payload",
          not find(recs, "echo_payload_mismatch") and not find(recs, "dropped_unexpected_echo"),
          "0 payload mismatches, 0 unexpected echoes")
    expected = [sender, "routerA" if sender == "host1" else "routerD", "routerB",
                "routerD" if sender == "host1" else "routerA", receiver]
    expected = expected + expected[-2::-1]
    first = sent[0]["message_id"] if sent else None
    got = path_of(recs, first)
    c.add("probe 1 traced through every router, out and back", got == expected, " → ".join(got))
    c.add("nothing forwarded by routerC (upper path only)", not find(recs, "forward", "routerC"))
    events, paths = trace(recs, first)
    return s, c, format_trace(events, paths) if events else ""


def path_lower(out):
    with Session(out, "path-lower", LOWER) as s:
        s.probe("host1", "host2", count=10, interval=0.1, timeout=1.0)
    recs = load_records(s.run_dir)
    sent = probes_sent(recs, "host1")
    answered = sum(outcome(recs, "host1", p["message_id"]) == "answered" for p in sent)
    c = Checks()
    c.add("10 probes answered over the lower path", answered == 10 and len(sent) == 10, f"{answered} of {len(sent)}")
    got = path_of(recs, sent[0]["message_id"]) if sent else []
    c.add("probe 1 went through routerC both ways",
          got == ["host1", "routerA", "routerC", "routerD", "host2", "routerD", "routerC", "routerA", "host1"],
          " → ".join(got))
    c.add("nothing forwarded by routerB", not find(recs, "forward", "routerB"))
    return s, c, ""


def hop_limit_loop(out):
    with Session(out, "hop-limit-loop", LOOP) as s:
        s.probe("host1", "routerD", count=1, timeout=1.0, hop_limit=6)
    recs = load_records(s.run_dir)
    forwards = [(r["device"], r["hop_limit"]) for r in find(recs, "forward")]
    drops = find(recs, "drop", reason="hop_limit_exhausted")
    c = Checks()
    c.add("routerA and routerB bounce the probe, hop limit 5 down to 1",
          forwards == [("routerA", 5), ("routerB", 4), ("routerA", 3), ("routerB", 2), ("routerA", 1)],
          ", ".join(f"{d} {h}" for d, h in forwards))
    c.add("routerB discards it: hop_limit_exhausted at hop limit 1",
          len(drops) == 1 and drops[0]["device"] == "routerB" and drops[0]["hop_limit"] == 1)
    c.add("host1 times out instead of waiting forever", len(find(recs, "probe_timeout", "host1")) == 1)
    sent = probes_sent(recs, "host1")
    events, paths = trace(recs, sent[0]["message_id"]) if sent else ([], [])
    return s, c, format_trace(events, paths) if events else ""


def error_handling(out):
    with Session(out, "error-handling", UPPER) as s:
        s.probe("host1", "routerB", count=1, timeout=1.0)
        s.garbage("routerA")
        s.probe("host1", "host2", count=1, timeout=1.0)
    recs = load_records(s.run_dir)
    sent = probes_sent(recs, "host1")
    unknown = sent[0]["message_id"] if sent else None
    c = Checks()
    c.add("unknown destination: routerA logs the reason and drops the probe",
          any(d["device"] == "routerA" and d.get("reason") == "unknown_destination" for d in drops_for(recs, unknown)))
    c.add("the waiting host times out", outcome(recs, "host1", unknown) == "timed out")
    c.add("invalid packet: routerA logs malformed_packet and drops it",
          bool(find(recs, "drop", "routerA", reason="malformed_packet")))
    c.add("the processes keep running: the next probe is answered",
          len(sent) == 2 and outcome(recs, "host1", sent[1]["message_id"]) == "answered")
    return s, c, ""


def link_failure(out):
    with Session(out, "link-failure", UPPER, link=LINK, loss=1.0) as s:
        s.probe("host1", "host2", count=3, interval=0.1, timeout=0.5)
    recs = load_records(s.run_dir)
    sent = probes_sent(recs, "host1")
    timeouts = find(recs, "probe_timeout", "host1")
    c = Checks()
    c.add("routerB–routerD down (100% loss): every probe is dropped at routerB",
          len(sent) == 3 and all(any(d["device"] == "routerB" for d in drops_for(recs, p["message_id"])) for p in sent))
    c.add("missing replies end in explicit timeouts, 0.5 s each", len(timeouts) == 3,
          f"{len(timeouts)} timeouts; host1 exit code {s.steps[0]['exit_code']}")
    return s, c, ""


def forced_drop(out):
    with Session(out, "forced-drop", UPPER, drop_rules=[("routerB", "type=PROBE,skip=2")]) as s:
        s.probe("host1", "host2", count=5, interval=0.1, timeout=0.5)
    recs = load_records(s.run_dir)
    results = [outcome(recs, "host1", p["message_id"]) for p in probes_sent(recs, "host1")]
    forced = find(recs, "drop_forced", "routerB")
    c = Checks()
    c.add("exactly probe 3 is dropped, by the forced rule at routerB",
          len(forced) == 1 and forced[0]["message_id"].endswith("-3"))
    c.add("probe 3 times out; the other 4 are answered",
          results == ["answered", "answered", "timed out", "answered", "answered"], ", ".join(results))
    c.add("no random drops", not find(recs, "drop_injected"))
    return s, c, ""


def random_loss(out):
    with Session(out, "random-loss", UPPER, seed=7, link=LINK, loss=0.3) as s:
        s.probe("host1", "host2", count=20, interval=0.05, timeout=0.5)
    recs = load_records(s.run_dir)
    sent = probes_sent(recs, "host1")
    timed_out = [p["message_id"] for p in sent if outcome(recs, "host1", p["message_id"]) == "timed out"]
    answered = [p["message_id"] for p in sent if outcome(recs, "host1", p["message_id"]) == "answered"]
    started = {r["device"]: r for r in find(recs, "started")}
    c = Checks()
    c.add("some probes lost at random, the rest answered",
          timed_out and answered and len(timed_out) + len(answered) == 20,
          f"{len(answered)} answered, {len(timed_out)} timed out")
    c.add("every timeout matches exactly one drop_injected; no answered probe was dropped",
          all(len([d for d in drops_for(recs, m) if d["event"] == "drop_injected"]) == 1 for m in timed_out)
          and not any(drops_for(recs, m) for m in answered))
    c.add("seeds and the lossy link are logged at startup",
          started.get("routerB", {}).get("seed") == 71 and
          {"neighbor": "routerD", "loss": 0.3, "delay_ms": 0} in started.get("routerB", {}).get("links", []),
          "routerB seed 71, routerD seed 73 (run seed 7)")
    return s, c, ""


def added_delay(out):
    with Session(out, "added-delay", UPPER, link=LINK, delay_ms=80) as s:
        s.probe("host1", "host2", count=10, interval=0.1, timeout=1.0)
    recs = load_records(s.run_dir)
    rtts = [r["rtt_ms"] for r in find(recs, "echo_received", "host1")]
    held = find(recs, "send_scheduled", delay_ms=80)
    c = Checks()
    c.add("all 10 answered, each round trip at least 2 × 80 ms",
          len(rtts) == 10 and min(rtts) >= 160,
          f"round trip {min(rtts):.1f} to {max(rtts):.1f} ms" if rtts else "no echoes")
    c.add("routerB and routerD hold each packet 80 ms on that link",
          {r["device"] for r in held} == {"routerB", "routerD"} and len(held) == 20)
    return s, c, ""


def late_response(out):
    with Session(out, "late-response", UPPER, link=LINK, delay_ms=300) as s:
        s.probe("host1", "host2", count=3, interval=0.2, timeout=0.5)
        s.pause(1.0)   # let the last late echo finish its trip before stopping
    recs = load_records(s.run_dir)
    sent = probes_sent(recs, "host1")
    c = Checks()
    late = []
    for p in sent:
        mid = p["message_id"]
        gave_up = find(recs, "probe_timeout", "host1", message_id=mid)
        echo_out = find(recs, "sent", "routerA", message_id=mid, type="ECHO", next_hop="host1")
        if gave_up and echo_out and echo_out[0]["timestamp"] > gave_up[0]["timestamp"]:
            late.append(round((echo_out[0]["timestamp"] - p["timestamp"]) * 1000))
    c.add("all 3 probes time out at 0.5 s (round trip about 600 ms)",
          len(find(recs, "probe_timeout", "host1")) == 3)
    c.add("no drop is logged for any of them", not any(drops_for(recs, p["message_id"]) for p in sent))
    c.add("each echo still reaches host1's link, after the timeout", len(late) == 3,
          "echo sent to host1 after " + ", ".join(f"{ms} ms" for ms in late))
    c.add("host1 logs the late echoes it receives as dropped_unexpected_echo",
          len(find(recs, "dropped_unexpected_echo", "host1")) >= 2)
    events, paths = trace(recs, sent[0]["message_id"]) if sent else ([], [])
    return s, c, format_trace(events, paths) if events else ""


SCENARIOS = [
    ("multihop-host1", "1. Multihop communication, from host1",
     lambda out: multihop(out, "host1", "host2", "multihop-host1")),
    ("multihop-host2", "1. Multihop communication, from host2",
     lambda out: multihop(out, "host2", "host1", "multihop-host2")),
    ("path-lower", "2. Configurable paths: lower path (upper path: scenario 1)", path_lower),
    ("hop-limit-loop", "3. Hop-limit enforcement on a forwarding loop", hop_limit_loop),
    ("error-handling", "4. Error handling: unknown destination, invalid packet", error_handling),
    ("link-failure", "4. Error handling: missing response after a link failure", link_failure),
    ("forced-drop", "5. Loss and delay: one forced drop", forced_drop),
    ("random-loss", "5. Loss and delay: random loss (30%, seed 7)", random_loss),
    ("added-delay", "5. Loss and delay: added delay (80 ms)", added_delay),
    ("late-response", "5. Loss and delay: a late response, told apart from a drop", late_response),
]


# ---------------------------------------------------------------- report

NOTES = {
    "forced-drop": "An injected drop leaves a `drop_forced` (or `drop_injected`) line at the router "
                   "that dropped the probe, and no echo is ever sent back.",
    "late-response": "Compare with a drop: here no router logs a drop. host1 gives up at 0.5 s, but "
                     "the echo is still forwarded and reaches host1 about 0.6 s after the probe; "
                     "host1 logs it as `dropped_unexpected_echo` because it is no longer waiting for it.",
    "link-failure": "The failed link is simulated by 100% loss on routerB–routerD, so every probe "
                    "is dropped there and logged with its reason.",
}


def setup_line(meta):
    """The network a scenario ran on, from its recorded settings."""
    parts = [f"`{meta['base_config']}`"]
    if meta.get("link"):
        change = []
        if meta.get("loss") is not None:
            change.append(f"{meta['loss']:.0%} loss")
        if meta.get("delay_ms") is not None:
            change.append(f"{meta['delay_ms']:g} ms delay")
        parts.append(f"{meta['link'].replace('-', '–')}: " + ", ".join(change))
    for router, rules in (meta.get("drop_rules") or {}).items():
        parts.append(f"drop rule on {router}: `{'; '.join(rules)}`")
    seeds = sorted((meta.get("router_seeds") or {}).values())
    if seeds:
        parts.append(f"run seed {meta['seed']} (router seeds {seeds[0]}–{seeds[-1]})")
    parts.append(f"replying host: {meta.get('responder')}")
    return "Network: " + "; ".join(parts) + "."

def write_report(out, results, neighbor_rows):
    rows = []
    for key, title, session, checks, _ in results:
        for row in checks.rows:
            rows.append({"scenario": key, "title": title, **row})
    rows += neighbor_rows
    with open(out / "summary.csv", "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["scenario", "title", "check", "passed", "detail"])
        writer.writeheader()
        writer.writerows(rows)

    total = len(rows)
    passed = sum(r["passed"] for r in rows)
    lines = [
        "# Core Acceptance Evidence",
        "",
        f"Generated by `python -m scripts.run_acceptance`: **{passed} of {total} checks passed.** "
        "Each scenario ran on the real network (separate processes, UDP on 127.0.0.1); its folder holds "
        "every device's log (`<device>.jsonl`), the exact topology (`config.json`) and the commands and "
        "seeds (`run.json`). The script starts the four routers and the replying host itself; the commands "
        "shown are the probes it sent. `python -m scripts.trace results/acceptance/<scenario>` lists a "
        "scenario's probes, and adding a probe number traces it hop by hop.",
        "",
        "Deterministic fault tests (forced drop, link failure, loop) are kept separate from the random-loss "
        "run, which has its own recorded seed.",
        "",
    ]
    for key, title, session, checks, trace_text in results:
        lines += [f"## {title}", "", f"Folder: `{key}/`. {setup_line(session.meta)}", ""]
        if key in NOTES:
            lines += [NOTES[key], ""]
        lines += ["```bash"] + [step["command"] for step in session.steps if step["step"] == "probe"]
        lines += [f"# plus raw bytes {step['bytes']!r} sent to {step['to']}"
                  for step in session.steps if step["step"] == "garbage"]
        lines += ["```", "", "| Check | Result | Detail |", "|---|---|---|"]
        for row in checks.rows:
            lines.append(f"| {row['check']} | {'PASS' if row['passed'] else 'FAIL'} | {row['detail']} |")
        lines.append("")
        if trace_text:
            lines += ["<details><summary>Trace of probe 1</summary>", "", "```text", trace_text, "```",
                      "", "</details>", ""]
    lines += ["## Every process sends only to configured neighbors", "",
              "Checked over every `sent` and `forward` line in all the scenarios above.", "",
              "| Check | Result | Detail |", "|---|---|---|"]
    for row in neighbor_rows:
        lines.append(f"| {row['check']} | {'PASS' if row['passed'] else 'FAIL'} | {row['detail']} |")
    (out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return passed, total


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run and check the core acceptance scenarios.")
    parser.add_argument("--out", default=OUT)
    args = parser.parse_args(argv)

    out = Path(args.out)
    existing = [key for key, _, _ in SCENARIOS if (out / key).exists()]
    if existing:
        print(f"error: {out / existing[0]} already exists; delete {out} to rerun", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)

    results = []
    try:
        for key, title, run in SCENARIOS:
            session, checks, trace_text = run(out)
            results.append((key, title, session, checks, trace_text))
            print(f"{'PASS' if checks.passed else 'FAIL'}  {title}")
            for row in checks.rows:
                if not row["passed"]:
                    print(f"      failed: {row['check']} ({row['detail']})")
    except (launch.LaunchError, launch.ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("If a port is in use, stop old processes with: "
              "pkill -f 'src.(router|host|reliable)'", file=sys.stderr)
        return 2

    violations = []
    sends = 0
    for key, *_ in results:
        violations += neighbor_violations(out / key)
        sends += sum(1 for r in load_records(out / key)
                     if r.get("event") in ("sent", "forward") and r.get("next_hop"))
    neighbor_rows = [{"scenario": "all", "title": "Neighbor-only sends",
                      "check": "every send and forward goes to a configured neighbor",
                      "passed": not violations,
                      "detail": f"{sends} sends and forwards checked" + (f"; violations: {violations[:5]}" if violations else "")}]
    print(f"{'PASS' if not violations else 'FAIL'}  Neighbor-only sends ({sends} checked)")

    passed, total = write_report(out, results, neighbor_rows)
    print(f"\n{passed} of {total} checks passed. Wrote {out / 'README.md'} and {out / 'summary.csv'}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
