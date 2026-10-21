
import json
from pathlib import Path


def load_config(path):
    """Load topology JSON and validate device definitions."""
    path = Path(path)

    try:
        with path.open("r", encoding="utf-8") as file:
            config = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load config {path}: {exc}") from exc

    if not isinstance(config, dict):
        raise ValueError("Configuration must be an object")

    devices = config.get("devices")
    if not isinstance(devices, dict) or not devices:
        raise ValueError("'devices' must be a nonempty object")

    ports = set()

    for name, device in devices.items():
        if not isinstance(device, dict):
            raise ValueError(f"{name}: device definition must be an object")

        if device.get("virtual_address") != name:
            raise ValueError(f"{name}: virtual_address must match device name")

        if device.get("role") not in ("host", "router"):
            raise ValueError(f"{name}: invalid role")

        host = device.get("udp_host")
        port = device.get("udp_port")
        neighbors = device.get("neighbors")

        if not isinstance(host, str) or not host:
            raise ValueError(f"{name}: invalid udp_host")

        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError(f"{name}: invalid udp_port")

        if port in ports and host == "127.0.0.1":
            raise ValueError(f"{name}: duplicate localhost UDP port")
        ports.add(port)

        if not isinstance(neighbors, list):
            raise ValueError(f"{name}: neighbors must be a list")

        if len(neighbors) != len(set(neighbors)):
            raise ValueError(f"{name}: duplicate neighbor")

        for neighbor in neighbors:
            if neighbor not in devices:
                raise ValueError(
                    f"{name}: unknown neighbor {neighbor}"
                )

    for name, device in devices.items():
        for neighbor in device["neighbors"]:
            if name not in devices[neighbor]["neighbors"]:
                raise ValueError(
                    f"Link {name}-{neighbor} is not bidirectional"
                )

    return config

