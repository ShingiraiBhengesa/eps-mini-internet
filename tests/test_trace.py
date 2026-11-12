
import json

import pytest

from scripts.trace import describe, format_trace, load_records, main, pick_message, probes, trace


def rec(t, device, event, mid=None, kind="PROBE", src="host1", dst="host2", hop=16, **extra):
    record = {"run_id": "t", "device": device, "timestamp": 100 + t / 1000, "event": event}
    if mid is not None:
        record.update(message_id=mid, type=kind, src=src, dst=dst, hop_limit=hop)
    record.update(extra)
    return record


def answered_probe(mid, t0=0):
    return [
        rec(t0 + 0, "host1", "sent", mid, next_hop="routerA"),
        rec(t0 + 1, "routerA", "received", mid),
        rec(t0 + 2, "routerA", "forward", mid, hop=15, next_hop="routerB"),
        rec(t0 + 3, "routerB", "received", mid, hop=15),
        rec(t0 + 4, "routerB", "forward", mid, hop=14, next_hop="routerD"),
        rec(t0 + 5, "routerB", "send_scheduled", mid, hop=14, next_hop="routerD", delay_ms=20),
        rec(t0 + 25, "routerD", "received", mid, hop=14),
        rec(t0 + 26, "host2", "received", mid, hop=13),
        rec(t0 + 27, "host2", "sent", mid, kind="ECHO", src="host2", dst="host1", next_hop="routerD"),
        rec(t0 + 28, "routerD", "received", mid, kind="ECHO", src="host2", dst="host1"),
        rec(t0 + 50, "host1", "received", mid, kind="ECHO", src="host2", dst="host1", hop=13),
        rec(t0 + 51, "host1", "echo_received", mid, kind="ECHO", src="host2", dst="host1", rtt_ms=51.0),
    ]


def dropped_probe(mid, t0):
    return [
        rec(t0 + 0, "host1", "sent", mid, next_hop="routerA"),
        rec(t0 + 1, "routerA", "received", mid),
        rec(t0 + 2, "routerA", "drop_forced", mid, next_hop="routerB", rule="type=PROBE,skip=0,count=1"),
        rec(t0 + 1000, "host1", "probe_timeout", mid, timeout_s=1.0),
    ]


def write_logs(folder, records):
    folder.mkdir(parents=True, exist_ok=True)
    by_device = {}
    for r in records:
        by_device.setdefault(r["device"], []).append(json.dumps(r))
    for device, lines in by_device.items():
        (folder / f"{device}.jsonl").write_text("\n".join(lines) + "\n")


def test_trace_follows_the_round_trip_in_time_order(tmp_path):
    write_logs(tmp_path, answered_probe("host1-aa-1"))
    records = load_records(tmp_path)

    events, path = trace(records, "host1-aa-1")

    assert [e["timestamp"] for e in events] == sorted(e["timestamp"] for e in events)
    assert path == ["host1", "routerA", "routerB", "routerD", "host2", "routerD", "host1"]
    text = format_trace(events, path)
    assert "holds PROBE 20 ms for the link to routerD" in text
    assert "round trip 51.0 ms" in text
    assert "dropped" not in text


def test_trace_marks_a_dropped_packet(tmp_path):
    write_logs(tmp_path, dropped_probe("host1-aa-2", 0))
    events, path = trace(load_records(tmp_path), "host1-aa-2")

    text = format_trace(events, path)
    assert path == ["host1", "routerA"]
    assert "DROPS PROBE on the link to routerB (forced rule type=PROBE,skip=0,count=1)" in text
    assert text.endswith("✗ dropped")


def test_probe_list_reports_each_outcome():
    records = answered_probe("host1-aa-1") + dropped_probe("host1-aa-2", 100)

    assert probes(records) == {
        "host1-aa-1": "answered in 51.0 ms",
        "host1-aa-2": "timed out (dropped at routerA, drop_forced)",
    }


def test_pick_message_by_number_or_full_id():
    records = answered_probe("host1-aa-1") + dropped_probe("host1-aa-2", 100)

    assert pick_message(records, "2") == "host1-aa-2"
    assert pick_message(records, "host1-aa-1") == "host1-aa-1"
    with pytest.raises(LookupError, match="no packet"):
        pick_message(records, "7")


def test_pick_message_number_seen_in_two_sessions():
    records = answered_probe("host1-aa-1") + answered_probe("host1-bb-1", 500)

    with pytest.raises(LookupError, match="more than once"):
        pick_message(records, "1")


@pytest.mark.parametrize("record,expected", [
    (rec(0, "routerA", "drop", "m", hop=1, reason="hop_limit_exhausted"),
     "DROPS PROBE host1→host2: hop_limit_exhausted"),
    (rec(0, "routerA", "drop", reason="malformed_packet", detail="Invalid JSON packet"),
     "DROPS a malformed packet: Invalid JSON packet"),
    (rec(0, "routerD", "drop_injected", "m", next_hop="routerB", loss=0.05),
     "DROPS PROBE on the link to routerB (random loss 5%)"),
    (rec(0, "routerB", "started", seed=11, drop_rules=[],
         links=[{"neighbor": "routerD", "loss": 0.05, "delay_ms": 20}]),
     "started, seed 11 | links: routerD 5% loss 20 ms | drop rules: none"),
    (rec(0, "routerB", "something_new", note="x"), "something_new {'note': 'x'}"),
])
def test_describe(record, expected):
    assert describe(record) == expected


def test_cli_lists_and_traces(tmp_path, capsys):
    write_logs(tmp_path, answered_probe("host1-aa-1"))

    assert main([str(tmp_path)]) == 0
    assert "host1-aa-1" in capsys.readouterr().out

    assert main([str(tmp_path), "1"]) == 0
    assert "path: host1 → routerA" in capsys.readouterr().out

    assert main([str(tmp_path / "empty")]) == 2
