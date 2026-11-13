"""Follow one packet through every device's log, hop by hop.

    python -m scripts.trace logs/demo          list the probes and DATA messages in a run
    python -m scripts.trace logs/demo 2        trace message number 2
    python -m scripts.trace logs/demo host1-3a3e3393-2

The folder is a run's log folder: LOG_DIR/RUN_ID for hand-run demos, or a
results/... run folder made by the launcher. A Track B message that was
resent is shown as one path per attempt.
"""

import argparse
import sys
from pathlib import Path

from scripts.metrics import read_jsonl
from src.reliable import parse_message_id

DROP_EVENTS = ("drop", "drop_injected", "drop_forced")


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


def label(record):
    """Packet type, plus the message number for Track B DATA and ACK."""
    kind = record.get("type", "")
    parsed = parse_message_id(record.get("message_id") or "")
    if kind in ("DATA", "ACK") and parsed:
        return f"{kind} #{parsed[2]}"
    return kind


def count(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def describe_track_b(record):
    """Plain-language text for Track B sender and receiver events, else None."""
    event, get = record.get("event"), record.get

    if event == "transfer_started":
        if get("mode") == "besteffort":
            return (f"starts a best-effort transfer: {get('messages')} messages to "
                    f"{get('dst')}, at most {get('rate'):g} per second, no ACKs")
        return (f"starts a stop-and-wait transfer: {get('messages')} messages to "
                f"{get('dst')}, {get('timeout_s'):g} s ACK timeout, "
                f"up to {get('retries')} resends each")
    if event == "retransmit":
        return f"RESENDS DATA #{get('seq')} (attempt {get('attempt')})"
    if event == "ack_received":
        return f"gets the ACK for #{get('seq')} after {get('rtt_ms'):.1f} ms"
    if event == "ack_timeout":
        return f"TIMEOUT: no ACK for #{get('seq')} within {get('timeout_s'):g} s"
    if event == "ack_ignored":
        return f"ignores an ACK: {get('reason')}"
    if event == "transfer_failed":
        return f"GIVES UP on #{get('seq')} after {get('attempts')} attempts"
    if event == "transfer_done":
        if get("mode") == "besteffort":
            return f"done: sent {get('messages')} messages in {get('elapsed_s'):.2f} s"
        if get("success"):
            return (f"done: {get('acked')}/{get('messages')} acknowledged, "
                    f"{count(get('retransmissions'), 'resend')}, {get('elapsed_s'):.2f} s")
        return (f"FAILED at #{get('failed_at')}: {get('acked')}/{get('messages')} "
                f"acknowledged, {get('elapsed_s'):.2f} s")
    if event == "delivered":
        return f"delivers DATA #{get('seq')} to the application"
    if event == "duplicate":
        action = "ACKs it again" if get("acked") else "ignored"
        return f"gets DATA #{get('seq')} again: not delivered twice, {action}"
    if event == "dropped_out_of_order":
        return f"ignores DATA #{get('seq')}: waiting for #{get('expected')}"
    if event == "transfer_received":
        return (f"has all {get('expected')} messages; keeps answering for "
                f"{get('linger_s'):g} s in case the last ACK was lost")
    if event == "receiver_done":
        reasons = {"linger_over": "waiting time over", "observation_over": "observation time over",
                   "stopped": "stopped"}
        total = f"/{get('expected')}" if get("expected") else ""
        return (f"stops ({reasons.get(get('reason'), get('reason'))}): "
                f"delivered {get('delivered')}{total}, {count(get('duplicates', 0), 'duplicate')}")
    if event == "dropped_unexpected_ack":
        return "ignores an ACK nothing is waiting for"
    if event == "dropped_bad_data_id":
        return "ignores DATA with an unreadable message ID"
    return None


def describe(record):
    """One plain-language line for a log record."""
    event = record.get("event", "?")
    kind = label(record)
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
        return "ignores an ECHO for a probe it is no longer waiting for (a late or stray reply)"
    if event == "dropped_wrong_destination":
        return f"ignores {kind} addressed to {record.get('dst')}"
    if event == "dropped_malformed":
        return f"ignores a malformed packet: {record.get('reason')}"
    if event == "dropped_unhandled_type":
        return f"ignores {kind}: hosts don't handle this type"

    text = describe_track_b(record)
    if text is not None:
        return text

    extra = {k: v for k, v in record.items()
             if k not in ("run_id", "device", "timestamp", "event")}
    return f"{event} {extra}" if extra else event


def messages(records):
    """Probes and DATA messages in the order first sent, with how each ended."""
    info = {}

    for r in records:
        mid, event, device = r.get("message_id"), r.get("event"), r.get("device", "")
        if mid is None:
            continue
        if event == "sent" and r.get("type") in ("PROBE", "DATA") and device == r.get("src"):
            entry = info.setdefault(mid, {"kind": r["type"], "attempts": 0, "drops": [],
                                          "result": None, "delivered": False})
            entry["attempts"] += 1
            continue
        entry = info.get(mid)
        if entry is None:
            continue
        if event in DROP_EVENTS:
            entry["drops"].append(f"{device}, {r.get('reason') or event}")
        elif event == "echo_received":
            entry["result"] = f"answered in {r['rtt_ms']:.1f} ms"
        elif event == "probe_timeout":
            entry["result"] = "timed out"
        elif event == "ack_received":
            entry["result"] = "acknowledged"
        elif event == "transfer_failed":
            entry["result"] = "gave up"
        elif event == "delivered":
            entry["delivered"] = True

    outcomes = {}
    for mid, entry in info.items():
        if entry["kind"] == "PROBE":
            text = entry["result"] or "no result logged"
        else:
            parts = (["delivered"] if entry["delivered"] else []) + \
                    ([entry["result"]] if entry["result"] else [])
            text = " and ".join(parts) or "lost"
            if entry["attempts"] > 1:
                text += f" after {entry['attempts']} attempts"
        if entry["drops"]:
            text += " (dropped at " + "; ".join(entry["drops"]) + ")"
        outcomes[mid] = text
    return outcomes


probes = messages  # earlier name, kept for callers


def pick_message(records, wanted):
    """Resolve a full message ID or a message number such as '2'."""
    ids = list(messages(records))
    if wanted in ids or any(r.get("message_id") == wanted for r in records):
        return wanted
    matches = [mid for mid in ids if mid.rsplit("-", 1)[-1] == wanted]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise LookupError(
            f"message {wanted} was sent more than once in this folder; use the full ID: "
            + ", ".join(matches)
        )
    raise LookupError(f"no packet {wanted!r} in this folder")


def trace(records, message_id):
    """The packet's events in time order, and its path for each attempt.

    Each path is {"devices": [...], "dropped": bool}. A probe has one
    attempt; a Track B message has one per time the sender sent it.
    """
    events = [r for r in records if r.get("message_id") == message_id]
    if not events:
        return [], []

    origin = events[0]["device"]
    paths = []
    for r in events:
        new_attempt = (r.get("event") == "sent" and r["device"] == origin
                       and r.get("src") == origin)
        if new_attempt or not paths:
            paths.append({"devices": [r["device"]], "dropped": False})
            continue
        path = paths[-1]
        if r.get("event") == "received" and r["device"] != path["devices"][-1]:
            path["devices"].append(r["device"])
        if r.get("event") in DROP_EVENTS:
            path["dropped"] = True
    return events, paths


def format_trace(events, paths):
    start = events[0]["timestamp"]
    lines = []
    for r in events:
        if r.get("event") == "send_scheduled" and not r.get("delay_ms"):
            continue  # a zero-delay hold adds nothing to read
        offset = (r["timestamp"] - start) * 1000
        lines.append(f"{offset:+9.1f} ms  {r['device']:<8} {describe(r)}")

    lines.append("")
    for number, path in enumerate(paths, 1):
        prefix = "path" if len(paths) == 1 else f"attempt {number}"
        ending = "  ✗ dropped" if path["dropped"] else ""
        lines.append(f"{prefix}: " + " → ".join(path["devices"]) + ending)
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Follow one packet through a run's logs.")
    parser.add_argument("folder", help="a run's log folder, e.g. logs/demo")
    parser.add_argument("message", nargs="?",
                        help="message number (e.g. 2) or full message ID; omit to list them")
    args = parser.parse_args(argv)

    records = load_records(args.folder)
    if not records:
        print(f"error: no .jsonl logs in {args.folder}", file=sys.stderr)
        return 2

    if args.message is None:
        found = messages(records)
        if not found:
            print("No probes or DATA messages in this folder.")
        for mid, outcome in found.items():
            print(f"{mid:<28} {outcome}")
        return 0

    try:
        message_id = pick_message(records, args.message)
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    events, paths = trace(records, message_id)
    print(f"Packet {message_id}\n")
    print(format_trace(events, paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
