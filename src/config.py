
import json
from pathlib import Path


class ConfigError(ValueError):
    """Raised when a topology configuration is invalid."""


def load_config(path):
    """Load topology JSON and validate it."""
    path = Path(path)

    try:
        with path.open("r", encoding="utf-8") as file:
            config = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot load config {path}: {exc}") from exc

    validate_config(config)
    return config


def validate_config(config):
    """Check devices, links, forwarding tables, and paths."""
    if not isinstance(config, dict):
        raise ConfigError("Configuration must be an object")

    devices = config.get("devices")
    if not isinstance(devices, dict) or not devices:
        raise ConfigError("'devices' must be a nonempty object")

    _validate_devices(devices)
    _validate_links(config.get("links"), devices)

    for name, device in devices.items():
        if device["role"] == "host":
            _validate_host(name, device)
        else:
            _validate_forwarding_table(name, device, devices)

    _validate_paths(config.get("paths"), devices)


def get_endpoint(config, name):
    """Return the (udp_host, udp_port) endpoint of a device."""
    device = config["devices"][name]
    return device["udp_host"], device["udp_port"]


def get_link(config, a, b):
    """Return the link entry connecting devices a and b."""
    for link in config["links"]:
        if set(link["endpoints"]) == {a, b}:
            return link
    raise ConfigError(f"No link between {a} and {b}")


def _validate_devices(devices):
    endpoints = set()

    for name, device in devices.items():
        if not isinstance(device, dict):
            raise ConfigError(f"{name}: device definition must be an object")

        if device.get("virtual_address") != name:
            raise ConfigError(f"{name}: virtual_address must match device name")

        if device.get("role") not in ("host", "router"):
            raise ConfigError(f"{name}: role must be 'host' or 'router'")

        host = device.get("udp_host")
        port = device.get("udp_port")
        neighbors = device.get("neighbors")

        if not isinstance(host, str) or not host:
            raise ConfigError(f"{name}: udp_host must be a nonempty string")

        if type(port) is not int or not 1 <= port <= 65535:
            raise ConfigError(f"{name}: udp_port must be an integer from 1 to 65535")

        if (host, port) in endpoints:
            raise ConfigError(f"{name}: duplicate UDP endpoint {host}:{port}")
        endpoints.add((host, port))

        if not isinstance(neighbors, list) or not neighbors:
            raise ConfigError(f"{name}: neighbors must be a nonempty list")

        if len(neighbors) != len(set(neighbors)):
            raise ConfigError(f"{name}: duplicate neighbor")

        for neighbor in neighbors:
            if neighbor not in devices:
                raise ConfigError(f"{name}: unknown neighbor {neighbor}")
            if neighbor == name:
                raise ConfigError(f"{name}: device cannot neighbor itself")

    for name, device in devices.items():
        for neighbor in device["neighbors"]:
            if name not in devices[neighbor]["neighbors"]:
                raise ConfigError(f"Link {name}-{neighbor} is not bidirectional")


def _validate_links(links, devices):
    if not isinstance(links, list):
        raise ConfigError("'links' must be a list")

    seen = set()

    for index, link in enumerate(links):
        where = f"links[{index}]"

        if not isinstance(link, dict):
            raise ConfigError(f"{where}: link must be an object")

        endpoints = link.get("endpoints")
        if (
            not isinstance(endpoints, list)
            or len(endpoints) != 2
            or endpoints[0] == endpoints[1]
        ):
            raise ConfigError(f"{where}: endpoints must name two different devices")

        a, b = endpoints
        for name in endpoints:
            if name not in devices:
                raise ConfigError(f"{where}: unknown device {name}")

        if b not in devices[a]["neighbors"]:
            raise ConfigError(f"{where}: {a} and {b} are not neighbors")

        pair = frozenset(endpoints)
        if pair in seen:
            raise ConfigError(f"{where}: duplicate link {a}-{b}")
        seen.add(pair)

        loss = link.get("loss")
        if type(loss) not in (int, float) or not 0 <= loss <= 1:
            raise ConfigError(f"{where}: loss must be a number from 0 to 1")

        delay = link.get("delay_ms")
        if type(delay) not in (int, float) or delay < 0:
            raise ConfigError(f"{where}: delay_ms must be a number >= 0")

    for name, device in devices.items():
        for neighbor in device["neighbors"]:
            if frozenset((name, neighbor)) not in seen:
                raise ConfigError(f"Neighbors {name}-{neighbor} have no link entry")


def _validate_host(name, device):
    if len(device["neighbors"]) != 1:
        raise ConfigError(f"{name}: host must have exactly one neighbor")

    if device.get("gateway") != device["neighbors"][0]:
        raise ConfigError(f"{name}: gateway must be the host's neighbor")

    if "forwarding_table" in device:
        raise ConfigError(f"{name}: hosts do not have a forwarding_table")


def _validate_forwarding_table(name, device, devices):
    table = device.get("forwarding_table")
    if not isinstance(table, dict) or not table:
        raise ConfigError(f"{name}: forwarding_table must be a nonempty object")

    for destination, next_hop in table.items():
        if destination not in devices:
            raise ConfigError(f"{name}: forwarding_table has unknown destination {destination}")
        if destination == name:
            raise ConfigError(f"{name}: forwarding_table cannot route to itself")
        if next_hop not in device["neighbors"]:
            raise ConfigError(
                f"{name}: next hop {next_hop} for {destination} is not a neighbor"
            )


def _trace_route(source, destination, devices):
    """Follow forwarding tables from a host to a destination."""
    route = [source, devices[source]["gateway"]]

    while route[-1] != destination:
        current = route[-1]
        device = devices[current]

        if device["role"] == "host":
            raise ConfigError(
                f"Route {source}->{destination} reaches host {current}"
            )

        next_hop = device["forwarding_table"].get(destination)
        if next_hop is None:
            raise ConfigError(f"{current}: no forwarding entry for {destination}")

        if next_hop in route:
            raise ConfigError(
                f"Route {source}->{destination} loops at {next_hop}"
            )
        route.append(next_hop)

    return route


def _validate_paths(paths, devices):
    if not isinstance(paths, dict):
        raise ConfigError("'paths' must be an object")

    for direction in ("forward", "return"):
        path = paths.get(direction)
        if not isinstance(path, list) or len(path) < 2:
            raise ConfigError(f"paths.{direction} must list at least two devices")

        for name in path:
            if name not in devices:
                raise ConfigError(f"paths.{direction}: unknown device {name}")

        source, destination = path[0], path[-1]
        for end in (source, destination):
            if devices[end]["role"] != "host":
                raise ConfigError(f"paths.{direction} must start and end at hosts")

        route = _trace_route(source, destination, devices)
        if route != path:
            raise ConfigError(
                f"paths.{direction} {path} does not match forwarding tables {route}"
            )

    if paths["forward"][0] != paths["return"][-1] or paths["forward"][-1] != paths["return"][0]:
        raise ConfigError("paths.return must go back from the forward destination to its source")
