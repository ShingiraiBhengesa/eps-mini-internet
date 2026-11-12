
import io
import json
import subprocess
import sys
import threading

from scripts.demo_net import LogFollower, feed_line, follow, main
from scripts.launch import Network


def line(**record):
    return json.dumps({"run_id": "d", "timestamp": 1.0, **record}) + "\n"


def test_follower_reads_only_new_complete_lines(tmp_path):
    log = tmp_path / "routerA.jsonl"
    log.write_text(line(device="routerA", event="started") + '{"device":"rou')
    follower = LogFollower(tmp_path)

    assert [r["event"] for r in follower.poll()] == ["started"]
    assert follower.poll() == []

    with open(log, "a") as file:
        file.write('terA","event":"forward","timestamp":2.0}\n')
    assert [r["event"] for r in follower.poll()] == ["forward"]


def test_follower_merges_files_in_time_order(tmp_path):
    (tmp_path / "host1.jsonl").write_text(line(device="host1", event="sent", timestamp=3.0))
    (tmp_path / "routerA.jsonl").write_text(line(device="routerA", event="forward", timestamp=2.0))

    assert [r["device"] for r in LogFollower(tmp_path).poll()] == ["routerA", "host1"]


def test_feed_hides_routine_router_steps_unless_asked():
    received = {"device": "routerA", "event": "received", "timestamp": 1.0,
                "message_id": "m", "type": "PROBE", "src": "host1", "dst": "host2", "hop_limit": 16}

    assert feed_line(received) is None
    assert "receives PROBE host1→host2" in feed_line(received, show_all=True)


def test_feed_line_without_color_has_no_escape_codes():
    record = {"device": "host1", "event": "probe_timeout", "timestamp": 1.0, "timeout_s": 1.0,
              "message_id": "m", "type": "PROBE", "src": "host1", "dst": "host2", "hop_limit": 16}

    assert "\033" not in feed_line(record)
    assert "\033[31m" in feed_line(record, color=True)


def test_live_feed_shows_a_probe_crossing_the_network(tmp_path, upper_config_on_free_ports):
    network = Network(upper_config_on_free_ports, "live", tmp_path, seed=0)
    follower = LogFollower(tmp_path / "live")
    out = io.StringIO()
    stop = threading.Event()
    feed = threading.Thread(target=follow, args=(follower, stop, out), kwargs={"interval": 0.05})

    with network:
        feed.start()
        subprocess.run([
            sys.executable, "-m", "src.host", "--name", "host1", "--probe", "host2",
            "--timeout", "1", "--config", str(upper_config_on_free_ports),
            "--run-id", "live", "--log-dir", str(tmp_path),
        ], check=True, capture_output=True)
    stop.set()
    feed.join(timeout=2)

    text = out.getvalue()
    for router in ("routerA", "routerB", "routerD"):
        assert f"{router:<8} forwards PROBE host1→host2" in text
    assert "gets the ECHO back" in text


def test_cli_refuses_to_reuse_a_run_folder(tmp_path, capsys):
    (tmp_path / "demo").mkdir()

    assert main(["--log-dir", str(tmp_path), "--run-id", "demo"]) == 2
    assert "--replace" in capsys.readouterr().err


def test_feed_colors_track_b_outcomes():
    base = {"device": "host1", "timestamp": 1.0}
    red, green = "\033[31m", "\033[32m"

    assert red in feed_line(dict(base, event="ack_timeout", seq=3, timeout_s=0.5), color=True)
    assert red in feed_line(dict(base, event="transfer_done", mode="stopwait", success=False,
                                 failed_at=1, acked=0, messages=3, elapsed_s=1.2), color=True)
    assert green in feed_line(dict(base, event="ack_received", seq=3, rtt_ms=1.2), color=True)
    assert green in feed_line(dict(base, event="transfer_done", mode="stopwait", success=True,
                                   acked=3, messages=3, retransmissions=0, elapsed_s=0.2), color=True)
