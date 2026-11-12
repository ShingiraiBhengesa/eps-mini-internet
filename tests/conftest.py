
import json
import socket

import pytest

from src.config import load_config


@pytest.fixture
def upper_config_on_free_ports(tmp_path):
    """Path to the upper-path topology moved onto ports nothing else is using."""
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
