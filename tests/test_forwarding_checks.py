
from copy import deepcopy

import pytest

from src.config import load_config
from src.forwarding_checks import ForwardingError, plan_forward


@pytest.fixture
def config():
    return deepcopy(load_config("config/topology.json"))


@pytest.fixture
def packet():
    return {
        "src": "host1",
        "dst": "host2",
        "hop_limit": 4,
        "type": "PROBE",
        "message_id": "p1",
        "payload": "hello",
    }


def test_correct_forwarding(config, packet):
    next_hop, endpoint, forwarded = plan_forward(
        config, "routerA", packet, ("127.0.0.1", 5001)
    )

    assert next_hop == "routerB"
    assert endpoint == ("127.0.0.1", 5102)
    assert forwarded["hop_limit"] == 3
    assert forwarded["payload"] == packet["payload"]


def test_original_packet_unchanged(config, packet):
    plan_forward(config, "routerA", packet)

    assert packet["hop_limit"] == 4


def test_hop_limit_exhausted(config, packet):
    packet["hop_limit"] = 1

    with pytest.raises(ForwardingError, match="hop_limit_exhausted"):
        plan_forward(config, "routerA", packet)


def test_zero_hop_limit(config, packet):
    packet["hop_limit"] = 0

    with pytest.raises(ForwardingError, match="hop_limit_exhausted"):
        plan_forward(config, "routerA", packet)


def test_unknown_destination(config, packet):
    packet["dst"] = "host9"

    with pytest.raises(ForwardingError, match="unknown_destination"):
        plan_forward(config, "routerA", packet)


def test_missing_forwarding_entry(config, packet):
    del config["devices"]["routerA"]["forwarding_table"]["host2"]

    with pytest.raises(ForwardingError, match="unknown_destination"):
        plan_forward(config, "routerA", packet)


def test_invalid_next_hop(config, packet):
    config["devices"]["routerA"]["forwarding_table"]["host2"] = "routerD"

    with pytest.raises(ForwardingError, match="invalid_next_hop"):
        plan_forward(config, "routerA", packet)


def test_unconfigured_sender(config, packet):
    with pytest.raises(ForwardingError, match="unconfigured_sender"):
        plan_forward(config, "routerA", packet, ("127.0.0.1", 5002))


def test_invalid_router(config, packet):
    with pytest.raises(ForwardingError, match="invalid_router"):
        plan_forward(config, "host1", packet)


def test_invalid_packet(config, packet):
    del packet["message_id"]

    with pytest.raises(ForwardingError, match="invalid_packet"):
        plan_forward(config, "routerA", packet)


def test_forwarding_loop_expires(config, packet):
    config["devices"]["routerB"]["forwarding_table"]["host2"] = "routerA"
    packet["hop_limit"] = 3

    _, _, packet = plan_forward(config, "routerA", packet)
    _, _, packet = plan_forward(config, "routerB", packet)

    with pytest.raises(ForwardingError, match="hop_limit_exhausted"):
        plan_forward(config, "routerA", packet)
