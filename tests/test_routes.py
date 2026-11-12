
import pytest

from scripts.make_routes import make_loop_config, make_route_config
from src.config import load_config, validate_config


@pytest.fixture
def base_config():
    return load_config("config/topology.json")


@pytest.mark.parametrize(
    "branch,middle",
    [("upper", "routerB"), ("lower", "routerC")]
)
def test_forward_and_return_routes(base_config, branch, middle):
    config = make_route_config(base_config, branch)

    assert config["paths"]["forward"] == [
        "host1", "routerA", middle, "routerD", "host2"
    ]

    assert config["paths"]["return"] == [
        "host2", "routerD", middle, "routerA", "host1"
    ]

    assert config["devices"]["routerA"]["forwarding_table"]["host2"] == middle
    assert config["devices"]["routerD"]["forwarding_table"]["host1"] == middle

    validate_config(config)


def test_upper_and_lower_routes_differ(base_config):
    upper = make_route_config(base_config, "upper")
    lower = make_route_config(base_config, "lower")

    assert upper["paths"]["forward"] != lower["paths"]["forward"]
    assert upper["paths"]["return"] != lower["paths"]["return"]


def test_original_configuration_unchanged(base_config):
    original = list(base_config["paths"]["forward"])

    make_route_config(base_config, "lower")

    assert base_config["paths"]["forward"] == original


def test_invalid_route_name(base_config):
    with pytest.raises(ValueError):
        make_route_config(base_config, "middle")


def test_loop_config_keeps_host_routes(base_config):
    config = make_loop_config(base_config)

    assert config["paths"] == base_config["paths"]
    assert config["devices"]["routerA"]["forwarding_table"]["routerD"] == "routerB"
    assert config["devices"]["routerB"]["forwarding_table"]["routerD"] == "routerA"


@pytest.mark.parametrize("path,build", [
    ("config/topology_upper.json", lambda base: make_route_config(base, "upper")),
    ("config/topology_lower.json", lambda base: make_route_config(base, "lower")),
    ("config/topology_loop.json", make_loop_config),
])
def test_committed_configs_match_generator(base_config, path, build):
    assert load_config(path) == build(base_config)
