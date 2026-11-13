
import json

import pytest

from scripts.trace import (
    describe, format_trace, load_records, main, messages, pick_message, probes, trace,
)


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

    events, paths = trace(records, "host1-aa-1")

    assert [e["timestamp"] for e in events] == sorted(e["timestamp"] for e in events)
    assert [p["devices"] for p in paths] == [
        ["host1", "routerA", "routerB", "routerD", "host2", "routerD", "host1"]
    ]
    text = format_trace(events, paths)
    assert "holds PROBE 20 ms for the link to routerD" in text
    assert "round trip 51.0 ms" in text
    assert "dropped" not in text


def test_trace_marks_a_dropped_packet(tmp_path):
    write_logs(tmp_path, dropped_probe("host1-aa-2", 0))
    events, paths = trace(load_records(tmp_path), "host1-aa-2")

    text = format_trace(events, paths)
    assert paths == [{"devices": ["host1", "routerA"], "dropped": True}]
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


def resent_data(mid="sw-t-3"):
    """DATA 3 dropped at routerB, then resent and acknowledged."""
    def data(t, device, event, **extra):
        return rec(t, device, event, mid, kind="DATA", **extra)

    def ack(t, device, event, **extra):
        return rec(t, device, event, mid, kind="ACK", src="host2", dst="host1", **extra)

    return [
        data(0, "host1", "sent", next_hop="routerA"),
        data(1, "routerA", "received"),
        data(2, "routerB", "received", hop=15),
        data(3, "routerB", "drop_forced", hop=14, next_hop="routerD", rule="type=DATA,skip=0,count=1"),
        data(300, "host1", "ack_timeout", seq=3, attempt=1, timeout_s=0.3),
        data(301, "host1", "retransmit", seq=3, attempt=2),
        data(301, "host1", "sent", next_hop="routerA"),
        data(302, "routerA", "received"),
        data(303, "routerB", "received", hop=15),
        data(325, "routerD", "received", hop=14),
        data(326, "host2", "received", hop=13),
        data(326, "host2", "delivered", seq=3, mode="stopwait"),
        ack(327, "host2", "sent", next_hop="routerD"),
        ack(328, "routerD", "received"),
        ack(350, "routerB", "received", hop=15),
        ack(351, "routerA", "received", hop=14),
        ack(352, "host1", "received", hop=13),
        ack(352, "host1", "ack_received", seq=3, attempt=2, rtt_ms=51.0),
    ]


def test_trace_shows_one_path_per_attempt_for_resent_data():
    events, paths = trace(resent_data(), "sw-t-3")

    assert paths == [
        {"devices": ["host1", "routerA", "routerB"], "dropped": True},
        {"devices": ["host1", "routerA", "routerB", "routerD", "host2", "routerD",
                     "routerB", "routerA", "host1"], "dropped": False},
    ]
    text = format_trace(events, paths)
    assert "attempt 1: host1 → routerA → routerB  ✗ dropped" in text
    assert "RESENDS DATA #3 (attempt 2)" in text
    assert "sends DATA #3 host1→host2 to routerA" in text


def test_message_list_includes_track_b_data():
    assert messages(resent_data()) == {
        "sw-t-3": "delivered and acknowledged after 2 attempts (dropped at routerB, drop_forced)",
    }
    assert pick_message(resent_data(), "3") == "sw-t-3"


@pytest.mark.parametrize("record,expected", [
    (rec(0, "host2", "duplicate", "sw-t-10", kind="DATA", seq=10, acked=True),
     "gets DATA #10 again: not delivered twice, ACKs it again"),
    (rec(0, "host1", "transfer_failed", "sw-t-1", kind="DATA", seq=1, attempts=6),
     "GIVES UP on #1 after 6 attempts"),
    (rec(0, "host1", "transfer_done", mode="stopwait", success=True, acked=10, messages=10,
         retransmissions=1, elapsed_s=1.234),
     "done: 10/10 acknowledged, 1 resend, 1.23 s"),
    (rec(0, "host2", "receiver_done", reason="linger_over", delivered=10, expected=10, duplicates=1),
     "stops (waiting time over): delivered 10/10, 1 duplicate"),
    (rec(0, "routerD", "forward", "sw-t-4", kind="ACK", src="host2", dst="host1", hop=15,
         next_hop="routerB"),
     "forwards ACK #4 host2→host1 to routerB, hop limit now 15"),
])
def test_describe_track_b_events(record, expected):
    assert describe(record) == expected


@pytest.mark.parametrize("record,expected", [
    ({"device": "host1", "timestamp": 1.0, "event": "transfer_started", "mode": "gobackn",
      "messages": 50, "dst": "host2", "window": 4, "timeout_s": 0.3, "retries": 5, "rate": 50},
     "starts a Go-Back-N transfer: 50 messages to host2, window 4, 0.3 s timeout, "
     "gives up after 5 timeouts in a row"),
    (rec(0, "host1", "ack_received", "gbn-t-7", kind="ACK", src="host2", dst="host1",
         seq=7, acked_through=7, newly_acked=3, rtt_ms=5.0),
     "gets a cumulative ACK through #7: 3 messages acknowledged at once"),
    (rec(0, "host1", "ack_timeout", "gbn-t-5", kind="DATA", seq=5, timeout_s=0.3, in_flight=4),
     "TIMEOUT: no ACK for #5 within 0.3 s; going back to resend 4 messages"),
    (rec(0, "host2", "dropped_out_of_order", "gbn-t-6", kind="DATA", seq=6, expected=5,
         acked_through=4),
     "discards DATA #6: waiting for #5, ACKs #4 again"),
    (rec(0, "host2", "duplicate", "gbn-t-3", kind="DATA", seq=3, acked=True, acked_through=5),
     "gets DATA #3 again: not delivered twice, ACKs #5 again"),
])
def test_describe_go_back_n_events(record, expected):
    assert describe(record) == expected
