
import argparse
import json
import socket

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


def free_port_config(tmp_path):
    """The upper-path topology moved onto ports nothing else is using."""
    config = load_config("config/topology_upper.json")
    sockets = []
    for device in config["devices"].values():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        sockets.append(sock)
        device["udp_port"] = sock.getsockname()[1]
    for sock in sockets:
        sock.close()

    path = tmp_path / "topology.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_run_experiment_end_to_end(tmp_path):
    config_path = free_port_config(tmp_path)
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
