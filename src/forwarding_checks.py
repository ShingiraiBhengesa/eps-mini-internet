
from .config import get_endpoint
from .packet import PacketError, validate_packet


class ForwardingError(ValueError):
    pass


def plan_forward(config, router_name, packet, sender_endpoint=None):
    devices = config["devices"]
    router = devices.get(router_name)

    if router is None or router["role"] != "router":
        raise ForwardingError("invalid_router")

    try:
        validate_packet(packet)
    except PacketError as exc:
        raise ForwardingError("invalid_packet") from exc

    if sender_endpoint is not None:
        allowed_endpoints = {
            get_endpoint(config, neighbor)
            for neighbor in router["neighbors"]
        }

        if tuple(sender_endpoint) not in allowed_endpoints:
            raise ForwardingError("unconfigured_sender")

    if packet["hop_limit"] <= 1:
        raise ForwardingError("hop_limit_exhausted")

    destination = packet["dst"]

    if destination not in devices:
        raise ForwardingError("unknown_destination")

    next_hop = router["forwarding_table"].get(destination)

    if next_hop is None:
        raise ForwardingError("unknown_destination")

    if next_hop not in devices or next_hop not in router["neighbors"]:
        raise ForwardingError("invalid_next_hop")

    forwarded = dict(packet)
    forwarded["hop_limit"] -= 1

    return next_hop, get_endpoint(config, next_hop), forwarded
