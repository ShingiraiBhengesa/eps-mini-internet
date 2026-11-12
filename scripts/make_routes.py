
import json
from copy import deepcopy
from pathlib import Path

from src.config import load_config, validate_config

# The forwarding-loop demo sends packets addressed to this router. The
# validator checks the host1<->host2 routes listed in "paths" and rejects
# loops there, so the loop is built on a destination those routes never use.
LOOP_DESTINATION = "routerD"


def make_route_config(base_config, branch):
    if branch not in ("upper", "lower"):
        raise ValueError("branch must be 'upper' or 'lower'")

    middle = "routerB" if branch == "upper" else "routerC"
    config = deepcopy(base_config)

    config["devices"]["routerA"]["forwarding_table"]["host2"] = middle
    config["devices"]["routerD"]["forwarding_table"]["host1"] = middle

    config["paths"]["forward"] = [
        "host1", "routerA", middle, "routerD", "host2"
    ]

    config["paths"]["return"] = [
        "host2", "routerD", middle, "routerA", "host1"
    ]

    validate_config(config)
    return config


def make_loop_config(base_config):
    """routerA and routerB send packets for routerD to each other.

    host1 -> routerA -> routerB -> routerA -> ... until the hop limit runs
    out. host1<->host2 traffic is routed exactly as in the base config.
    """
    config = deepcopy(base_config)

    config["devices"]["routerA"]["forwarding_table"][LOOP_DESTINATION] = "routerB"
    config["devices"]["routerB"]["forwarding_table"][LOOP_DESTINATION] = "routerA"

    validate_config(config)
    return config


def write_config(path, config):
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"Created {path}")


def main():
    base = load_config("config/topology.json")

    for branch in ("upper", "lower"):
        write_config(
            Path(f"config/topology_{branch}.json"),
            make_route_config(base, branch),
        )

    write_config(Path("config/topology_loop.json"), make_loop_config(base))


if __name__ == "__main__":
    main()
