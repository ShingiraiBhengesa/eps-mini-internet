
import argparse
import io
import json

import pytest

from src.run_log import create_logger, log_path, run_id_arg


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


def test_log_file_is_written_alongside_stream(tmp_path):
    stream = io.StringIO()
    path = log_path(tmp_path, "run-006", "routerA")
    log = create_logger("run-006", stream, path=path)

    log("routerA", "started", seed=3)
    log("routerA", "forward", make_packet(), next_hop="routerB")
    log.close()

    assert path == tmp_path / "run-006" / "routerA.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event"] for line in lines] == ["started", "forward"]
    assert stream.getvalue().splitlines() == lines


def test_log_file_created_only_on_first_write(tmp_path):
    path = log_path(tmp_path, "run-007", "host1")
    log = create_logger("run-007", io.StringIO(), path=path)

    assert not path.exists()
    log("host1", "started")
    log.close()
    assert path.exists()


@pytest.mark.parametrize("value", ["baseline-d20-l5-s3", "run.1", "A_b"])
def test_run_id_accepts_simple_names(value):
    assert run_id_arg(value) == value


@pytest.mark.parametrize("value", ["", "../escape", "a/b", "-starts-with-dash", "x" * 65])
def test_run_id_rejects_unsafe_names(value):
    with pytest.raises(argparse.ArgumentTypeError):
        run_id_arg(value)
