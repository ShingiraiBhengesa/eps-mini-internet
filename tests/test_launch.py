
import argparse
import json

import pytest

from scripts.launch import (
    ROUTERS,
    apply_link_settings,
    router_drop_rule_arg,
    router_seed,
    run_experiment,
)
from scripts.metrics import run_metrics
from src.config import ConfigError, get_link, load_config


@pytest.fixture
def base_config():
    return load_config("config/topology_upper.json")


def test_apply_link_settings_changes_only_that_link(base_config):
    config = apply_link_settings(base_config, "routerD-routerB", loss=0.05, delay_ms=20)

    assert get_link(config, "routerB", "routerD") == {
        "endpoints": ["routerB", "routerD"], "loss": 0.05, "delay_ms": 20,
    }
    others = [link for link in config["links"] if set(link["endpoints"]) != {"routerB", "routerD"}]
    assert all(link["loss"] == 0.0 and link["delay_ms"] == 0 for link in others)
    assert get_link(base_config, "routerB", "routerD")["loss"] == 0.0


@pytest.mark.parametrize("link,loss", [
    ("routerB", None),
    ("host1-host2", None),
    ("routerB-routerD", 1.5),
])
def test_apply_link_settings_rejects_bad_input(base_config, link, loss):
    with pytest.raises(ConfigError):
        apply_link_settings(base_config, link, loss=loss)


def test_router_seeds_differ_within_and_across_runs():
    seeds = [router_seed(run, i) for run in (1, 2, 3) for i in range(len(ROUTERS))]
    assert len(seeds) == len(set(seeds))


def test_router_drop_rule_arg():
    assert router_drop_rule_arg("routerB:type=PROBE,skip=1") == ("routerB", "type=PROBE,skip=1")

    for bad in ("type=PROBE", "routerZ:type=PROBE", "routerB:colour=red"):
        with pytest.raises(argparse.ArgumentTypeError):
            router_drop_rule_arg(bad)


def test_run_experiment_end_to_end(tmp_path, upper_config_on_free_ports):
    config_path = upper_config_on_free_ports
    out = tmp_path / "runs"

    run_dir, meta = run_experiment(
        config_path, "e2e-1", 5, out,
        link="routerB-routerD", loss=0.0, delay_ms=5,
        count=3, interval=0.05, timeout=0.5,
        drop_rules=[("routerB", "type=PROBE,skip=1")],
        condition="test",
    )

    assert meta["sender_exit_code"] == 1
    assert meta["sender_summary"] == "2/3 probes answered"
    assert meta["router_seeds"]["routerB"] == router_seed(5, 1)

    saved = json.loads((run_dir / "run.json").read_text())
    assert saved["drop_rules"] == {"routerB": ["type=PROBE,skip=1"]}
    assert get_link(load_config(run_dir / "config.json"), "routerB", "routerD")["delay_ms"] == 5

    for device in ("host1", "host2", *ROUTERS):
        assert (run_dir / f"{device}.jsonl").exists()
    assert not list(run_dir.glob("*.stderr.txt"))

    started = [
        json.loads(line)
        for line in (run_dir / "routerB.jsonl").read_text().splitlines()
        if '"event":"started"' in line
    ][0]
    assert started["seed"] == router_seed(5, 1)
    assert {"neighbor": "routerD", "loss": 0.0, "delay_ms": 5} in started["links"]

    metrics = run_metrics(run_dir)
    assert (metrics["probes"], metrics["answered"], metrics["timeouts"]) == (3, 2, 1)
    assert metrics["drops_forced"] == 1
    assert metrics["complete"]
    assert all(rtt >= 10 for rtt in metrics["rtts"])  # 5 ms each way on B-D


def test_run_experiment_refuses_to_overwrite(tmp_path):
    (tmp_path / "taken").mkdir()

    with pytest.raises(RuntimeError, match="already exists"):
        run_experiment("config/topology_upper.json", "taken", 1, tmp_path)


def test_run_transfer_end_to_end(tmp_path, upper_config_on_free_ports):
    from scripts.launch import run_transfer

    run_dir, meta = run_transfer(
        upper_config_on_free_ports, "xfer-1", 3, tmp_path / "runs",
        mode="stopwait", count=4, rate=100, timeout=0.3, retries=3, linger=0.5,
        link="routerB-routerD", loss=0.0, delay_ms=5,
        drop_rules=[("routerB", "type=DATA,message_id=sw-xfer-1-2")],
    )

    assert meta["sender_exit_code"] == 0
    assert meta["receiver_exited_by_itself"] is True
    m = run_metrics(run_dir)
    assert m["kind"] == "transfer"
    assert (m["delivered"], m["full_sequence"], m["in_order"]) == (4, True, True)
    assert (m["retransmissions"], m["data_drops"], m["duplicates_at_receiver"]) == (1, 1, 0)
    assert m["sender_success"] is True
    assert m["receiver_stop"] == "linger_over"
    assert m["delivery_time_s"] > 0


def test_run_transfer_to_a_host_that_is_down(tmp_path, upper_config_on_free_ports):
    from scripts.launch import run_transfer

    run_dir, meta = run_transfer(
        upper_config_on_free_ports, "down-1", 1, tmp_path / "runs",
        count=3, timeout=0.1, retries=2, start_receiver=False,
    )

    m = run_metrics(run_dir)
    assert meta["sender_exit_code"] == 1
    assert (m["sender_success"], m["failed_at"], m["retransmissions"]) == (False, 1, 2)
    assert m["delivered"] == 0
    assert not (run_dir / "host2.jsonl").exists()
