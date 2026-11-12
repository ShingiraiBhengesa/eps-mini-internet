"""Follow one packet through every device's log, hop by hop.

    python -m scripts.trace logs/demo          list the probes in a run
    python -m scripts.trace logs/demo 2        trace probe number 2
    python -m scripts.trace logs/demo host1-3a3e3393-2

The folder is a run's log folder: LOG_DIR/RUN_ID for hand-run demos, or a
results/... run folder made by the launcher.
"""

import argparse
import sys
from pathlib import Path

from scripts.metrics import read_jsonl


def is_router(device):
    return device.startswith("router")


def load_records(run_dir):
    """Every log record in a run folder, oldest first."""
    records = []
    for path in sorted(Path(run_dir).glob("*.jsonl")):
        records.extend(read_jsonl(path))
    return sorted(records, key=lambda r: r.get("timestamp", 0))


def route(record):
    return f"{record.get('src')}→{record.get('dst')}"


def percent(value):
    return f"{value * 100:g}%"


def describe(record):
    """One plain-language line for a log record."""
    event = record.get("event", "?")
    kind = record.get("type", "")
    hop = record.get("hop_limit")
    next_hop = record.get("next_hop")

    if event == "started":
        if "links" in record:
            links = ", ".join(
                f"{l['neighbor']} {percent(l['loss'])} loss {l['delay_ms']:g} ms"
                for l in record["links"]
            )
            rules = "; ".join(record.get("drop_rules") or []) or "none"
            return f"started, seed {record.get('seed')} | links: {links} | drop rules: {rules}"
        return (f"started ({record.get('mode')} mode), gateway {record.get('gateway')}, "
                f"hop limit {record.get('hop_limit')}")
    if event == "sent" and not is_router(record.get("device", "")):
        return f"sends {kind} {route(record)} to {next_hop} (hop limit {hop})"
    if event == "sent":
        return f"sends {kind} on to {next_hop}"
    if event == "received":
        return f"receives {kind} {route(record)} (hop limit {hop})"
    if event == "forward":
        return f"forwards {kind} {route(record)} to {next_hop}, hop limit now {hop}"
    if event == "send_scheduled":
        return f"holds {kind} {record.get('delay_ms', 0):g} ms for the link to {next_hop}"
    if event == "drop":
        if record.get("reason") == "malformed_packet":
            return f"DROPS a malformed packet: {record.get('detail')}"
        return f"DROPS {kind} {route(record)}: {record.get('reason')}"
    if event == "drop_injected":
        return f"DROPS {kind} on the link to {next_hop} (random loss {percent(record.get('loss', 0))})"
    if event == "drop_forced":
        return f"DROPS {kind} on the link to {next_hop} (forced rule {record.get('rule')})"
    if event == "echo_received":
        return f"gets the ECHO back: round trip {record.get('rtt_ms'):.1f} ms"
    if event == "probe_timeout":
        return f"TIMEOUT: no echo within {record.get('timeout_s')} s"
    if event == "echo_payload_mismatch":
        return "ignores an ECHO whose payload doesn't match the probe"
    if event == "dropped_unexpected_echo":
        return "ignores an ECHO for a different probe"
    if event == "dropped_wrong_destination":
        return f"ignores {kind} addressed to {record.get('dst')}"
    if event == "dropped_malformed":
        return f"ignores a malformed packet: {record.get('reason')}"
    if event == "dropped_unhandled_type":
        return f"ignores {kind}: hosts only handle PROBE and ECHO"

    extra = {k: v for k, v in record.items()
             if k not in ("run_id", "device", "timestamp", "event")}
    return f"{event} {extra}" if extra else event


DROP_EVENTS = ("drop", "drop_injected", "drop_forced")


def probes(records):
    """Probe IDs in the order they were sent, with how each one ended."""
    result, dropped = {}, {}

    for r in records:
        mid, event = r.get("message_id"), r.get("event")
        if event == "sent" and r.get("type") == "PROBE" and not is_router(r["device"]):
            result.setdefault(mid, "no result logged")
        elif mid in result:
            if event in DROP_EVENTS:
                dropped[mid] = f"{r['device']}, {r.get('reason') or event}"
            elif event == "echo_received":
                result[mid] = f"answered in {r['rtt_ms']:.1f} ms"
            elif event == "probe_timeout":
                result[mid] = "timed out"

    return {
        mid: outcome + (f" (dropped at {dropped[mid]})" if mid in dropped else "")
        for mid, outcome in result.items()
    }


def pick_message(records, wanted):
    """Resolve a full message ID or a probe number such as '2'."""
    ids = list(probes(records))
    if wanted in ids or any(r.get("message_id") == wanted for r in records):
        return wanted
    matches = [mid for mid in ids if mid.rsplit("-", 1)[-1] == wanted]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise LookupError(
            f"probe {wanted} was sent more than once in this folder; use the full ID: "
            + ", ".join(matches)
        )
    raise LookupError(f"no packet {wanted!r} in this folder")


def trace(records, message_id):
    """The packet's events in time order, plus its path as a list of devices."""
    events = [r for r in records if r.get("message_id") == message_id]
    if not events:
        return [], []

    path = [events[0]["device"]]
    for r in events[1:]:
        if r.get("event") == "received" and r["device"] != path[-1]:
            path.append(r["device"])
    return events, path


def format_trace(events, path):
    start = events[0]["timestamp"]
    lines = []
    for r in events:
        if r.get("event") == "send_scheduled" and not r.get("delay_ms"):
            continue  # a zero-delay hold adds nothing to read
        offset = (r["timestamp"] - start) * 1000
        lines.append(f"{offset:+9.1f} ms  {r['device']:<8} {describe(r)}")

    ending = ""
    if any(r.get("event") in DROP_EVENTS for r in events):
        ending = "  ✗ dropped"
    lines.append("")
    lines.append("path: " + " → ".join(path) + ending)
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Follow one packet through a run's logs.")
    parser.add_argument("folder", help="a run's log folder, e.g. logs/demo")
    parser.add_argument("message", nargs="?",
                        help="probe number (e.g. 2) or full message ID; omit to list probes")
    args = parser.parse_args(argv)

    records = load_records(args.folder)
    if not records:
        print(f"error: no .jsonl logs in {args.folder}", file=sys.stderr)
        return 2

    if args.message is None:
        found = probes(records)
        if not found:
            print("No probes in this folder.")
        for mid, outcome in found.items():
            print(f"{mid:<24} {outcome}")
        return 0

    try:
        message_id = pick_message(records, args.message)
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    events, path = trace(records, message_id)
    print(f"Packet {message_id}\n")
    print(format_trace(events, path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
