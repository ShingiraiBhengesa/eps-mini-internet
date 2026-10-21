
import io
import json

from src.run_log import create_logger


def make_packet():
    return {
        "src": "host1",
        "dst": "host2",
        "type": "PROBE",
        "message_id": "p1",
        "hop_limit": 8,
        "payload": "hello",
    }


def test_log_contains_required_fields():
    stream = io.StringIO()
    log = create_logger("run-001", stream)

    log("routerA", "forward", make_packet(), next_hop="routerB")

    record = json.loads(stream.getvalue())

    assert record["run_id"] == "run-001"
    assert record["device"] == "routerA"
    assert record["event"] == "forward"
    assert record["message_id"] == "p1"
    assert record["type"] == "PROBE"
    assert record["src"] == "host1"
    assert record["dst"] == "host2"
    assert record["hop_limit"] == 8
    assert record["next_hop"] == "routerB"
    assert isinstance(record["timestamp"], float)


def test_log_packet_drop():
    stream = io.StringIO()
    log = create_logger("run-002", stream)

    log(
        "routerB",
        "drop",
        make_packet(),
        reason="hop_limit_exhausted"
    )

    record = json.loads(stream.getvalue())

    assert record["event"] == "drop"
    assert record["reason"] == "hop_limit_exhausted"
    assert record["hop_limit"] == 8


def test_log_timeout():
    stream = io.StringIO()
    log = create_logger("run-003", stream)

    log("host1", "timeout", make_packet(), timeout_s=2.0)

    record = json.loads(stream.getvalue())

    assert record["device"] == "host1"
    assert record["event"] == "timeout"
    assert record["timeout_s"] == 2.0


def test_log_without_packet():
    stream = io.StringIO()
    log = create_logger("run-004", stream)

    log("routerA", "started")

    record = json.loads(stream.getvalue())

    assert record["run_id"] == "run-004"
    assert record["device"] == "routerA"
    assert record["event"] == "started"


def test_multiple_log_entries():
    stream = io.StringIO()
    log = create_logger("run-005", stream)

    log("routerA", "received", make_packet())
    log("routerA", "forward", make_packet(), next_hop="routerB")

    records = [
        json.loads(line)
        for line in stream.getvalue().splitlines()
    ]

    assert len(records) == 2
    assert records[0]["event"] == "received"
    assert records[1]["event"] == "forward"
