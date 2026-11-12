
import json

import pytest

from scripts.metrics import condition_metrics, read_jsonl, run_metrics, summarize


def write_run(root, run_id, *, seed, rtts, timeouts, condition="c1",
              count=None, router_events=(), meta_extra=None):
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    count = len(rtts) + timeouts if count is None else count

    meta = {
        "run_id": run_id, "condition": condition, "seed": seed,
        "link": "routerB-routerD", "loss": 0.05, "delay_ms": 20,
        "sender": "host1", "count": count, "sender_exit_code": 0,
    }
    meta.update(meta_extra or {})
    (run_dir / "run.json").write_text(json.dumps(meta))

    lines = []
    for i, rtt in enumerate(rtts):
        lines.append({"event": "sent", "type": "PROBE", "message_id": f"m{i}"})
        lines.append({"event": "echo_received", "type": "ECHO", "rtt_ms": rtt})
    for i in range(timeouts):
        lines.append({"event": "sent", "type": "PROBE", "message_id": f"t{i}"})
        lines.append({"event": "probe_timeout", "type": "PROBE"})
    (run_dir / "host1.jsonl").write_text("\n".join(json.dumps(r) for r in lines) + "\n")

    (run_dir / "routerB.jsonl").write_text(
        "".join(json.dumps({"event": e}) + "\n" for e in router_events)
    )
    return run_dir


def test_run_metrics_counts_and_rtt(tmp_path):
    run_dir = write_run(
        tmp_path, "r1", seed=1, rtts=[40.0, 42.0, 44.0], timeouts=1,
        router_events=["started", "drop_injected", "drop_forced", "forward"],
    )

    m = run_metrics(run_dir)

    assert (m["probes"], m["answered"], m["timeouts"]) == (4, 3, 1)
    assert m["success_fraction"] == 0.75
    assert m["rtt_mean_ms"] == 42.0
    assert m["rtt_sd_ms"] == 2.0
    assert (m["rtt_min_ms"], m["rtt_median_ms"], m["rtt_max_ms"]) == (40.0, 42.0, 44.0)
    assert (m["drops_injected"], m["drops_forced"], m["drops_other"]) == (1, 1, 0)
    assert m["complete"]


def test_run_with_no_answers_is_kept(tmp_path):
    m = run_metrics(write_run(tmp_path, "r1", seed=1, rtts=[], timeouts=3))

    assert m["success_fraction"] == 0.0
    assert m["rtt_mean_ms"] is None
    assert m["complete"]


def test_unfinished_run_is_flagged(tmp_path):
    m = run_metrics(write_run(tmp_path, "r1", seed=1, rtts=[40.0], timeouts=0, count=20))
    assert not m["complete"]

    m = run_metrics(write_run(
        tmp_path, "r2", seed=2, rtts=[40.0], timeouts=0,
        meta_extra={"launch_error": "routerB exited"},
    ))
    assert not m["complete"]


def test_condition_metrics_pool_runs(tmp_path):
    runs = [
        run_metrics(write_run(tmp_path, "a1", seed=1, rtts=[40.0, 40.0], timeouts=0)),
        run_metrics(write_run(tmp_path, "a2", seed=2, rtts=[50.0], timeouts=1)),
        run_metrics(write_run(tmp_path, "b1", seed=3, rtts=[90.0], timeouts=0, condition="c2",
                              meta_extra={"delay_ms": 80})),
    ]

    rows = condition_metrics(runs)

    assert [r["condition"] for r in rows] == ["c1", "c2"]
    c1 = rows[0]
    assert (c1["runs"], c1["seeds"], c1["probes"], c1["answered"], c1["timeouts"]) == (
        2, "1 2", 4, 3, 1,
    )
    assert c1["success_fraction"] == 0.75
    assert c1["success_sd"] == pytest.approx(0.3536, abs=1e-4)
    assert c1["rtt_mean_ms"] == pytest.approx(43.333, abs=1e-3)


def test_torn_last_line_is_skipped(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text('{"event":"started"}\n{"event":"se')

    assert read_jsonl(path) == [{"event": "started"}]


def test_summarize_writes_csv_files(tmp_path):
    write_run(tmp_path, "r1", seed=1, rtts=[40.0], timeouts=0)

    runs, conditions = summarize(tmp_path)

    assert len(runs) == 1 and len(conditions) == 1
    header = (tmp_path / "runs.csv").read_text().splitlines()[0]
    assert header.startswith("run_id,condition,seed")
    assert "rtts" not in header
    assert (tmp_path / "conditions.csv").exists()
