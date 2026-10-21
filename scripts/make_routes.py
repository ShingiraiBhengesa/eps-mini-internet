
import json
from copy import deepcopy
from pathlib import Path

from src.config import load_config, validate_config


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


def main():
    base = load_config("config/topology.json")

    for branch in ("upper", "lower"):
        path = Path(f"config/topology_{branch}.json")
        config = make_route_config(base, branch)
        path.write_text(
            json.dumps(config, indent=2) + "\n",
            encoding="utf-8"
        )
        print(f"Created {path}")


if __name__ == "__main__":
    main()
