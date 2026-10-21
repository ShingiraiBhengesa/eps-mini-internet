
import json

import pytest

from src.config import (
    ConfigError,
    get_endpoint,
    get_link,
    load_config,
    validate_config,
)

CONFIG_PATH = "config/topology.json"


@pytest.fixture
def config():
    with open(CONFIG_PATH, encoding="utf-8") as file:
        return json.load(file)


def test_repository_config_is_valid():
    config = load_config(CONFIG_PATH)

    roles = [device["role"] for device in config["devices"].values()]
    assert roles.count("host") == 2
    assert roles.count("router") == 4


def test_helpers(config):
    assert get_endpoint(config, "host1") == ("127.0.0.1", 5001)
    assert get_link(config, "routerB", "routerA")["loss"] == 0.0

    with pytest.raises(ConfigError):
        get_link(config, "host1", "host2")


def test_missing_file():
    with pytest.raises(ConfigError):
        load_config("config/does-not-exist.json")


def test_invalid_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json")

    with pytest.raises(ConfigError):
        load_config(path)


def test_config_error_is_value_error():
    assert issubclass(ConfigError, ValueError)


def reject(config, match):
    with pytest.raises(ConfigError, match=match):
        validate_config(config)


def test_reject_mismatched_virtual_address(config):
    config["devices"]["host1"]["virtual_address"] = "hostX"
    reject(config, "virtual_address")


def test_reject_invalid_role(config):
    config["devices"]["routerA"]["role"] = "switch"
    reject(config, "role")


def test_reject_invalid_port(config):
    config["devices"]["routerA"]["udp_port"] = 70000
    reject(config, "udp_port")


def test_reject_duplicate_endpoint(config):
    config["devices"]["routerA"]["udp_port"] = 5001
    reject(config, "duplicate UDP endpoint")


def test_reject_unknown_neighbor(config):
    config["devices"]["routerA"]["neighbors"].append("routerZ")
    reject(config, "unknown neighbor")


def test_reject_one_way_neighbor(config):
    config["devices"]["routerB"]["neighbors"].remove("routerA")
    reject(config, "not bidirectional")


def test_reject_missing_link_entry(config):
    config["links"].pop(1)
    reject(config, "no link entry")


def test_reject_link_between_non_neighbors(config):
    config["links"].append(
        {"endpoints": ["routerB", "routerC"], "loss": 0.0, "delay_ms": 0}
    )
    reject(config, "not neighbors")


def test_reject_invalid_loss(config):
    config["links"][0]["loss"] = 1.5
    reject(config, "loss")


def test_reject_negative_delay(config):
    config["links"][0]["delay_ms"] = -1
    reject(config, "delay_ms")


def test_reject_host_with_wrong_gateway(config):
    config["devices"]["host1"]["gateway"] = "routerB"
    reject(config, "gateway")


def test_reject_missing_forwarding_table(config):
    del config["devices"]["routerA"]["forwarding_table"]
    reject(config, "forwarding_table")


def test_reject_next_hop_not_neighbor(config):
    config["devices"]["routerA"]["forwarding_table"]["host2"] = "routerD"
    reject(config, "not a neighbor")


def test_reject_unknown_destination(config):
    config["devices"]["routerA"]["forwarding_table"]["host9"] = "routerB"
    reject(config, "unknown destination")


def test_reject_forwarding_loop(config):
    config["devices"]["routerB"]["forwarding_table"]["host2"] = "routerA"
    reject(config, "loops")


def test_reject_missing_route(config):
    del config["devices"]["routerB"]["forwarding_table"]["host2"]
    reject(config, "no forwarding entry")


def test_reject_path_not_matching_tables(config):
    config["paths"]["forward"] = [
        "host1", "routerA", "routerC", "routerD", "host2"
    ]
    reject(config, "does not match")


def test_reject_return_path_wrong_ends(config):
    config["paths"]["return"] = list(config["paths"]["forward"])
    reject(config, "paths.return")


def test_reject_path_starting_at_router(config):
    config["paths"]["forward"] = ["routerA", "routerB", "routerD", "host2"]
    reject(config, "start and end at hosts")
