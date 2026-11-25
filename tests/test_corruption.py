import json
import pytest
from unittest.mock import Mock

from src.packet import (
    PacketError,
    encode_packet,
    decode_packet,
)
from src.transport import corrupt_packet, send_packet
from src.conditions import CorruptRule


def make_packet(packet_type="DATA"):
    return {
        "src": "host1",
        "dst": "host2",
        "hop_limit": 16,
        "type": packet_type,
        "message_id": "sw-test-1",
        "payload": "Hello!" if packet_type == "DATA" else "",
    }


def test_corruption_changes_payload():
    encoded = encode_packet(make_packet())
    corrupted = corrupt_packet(encoded)

    original = json.loads(encoded)
    modified = json.loads(corrupted)

    assert original["payload"] != modified["payload"]


def test_corruption_preserves_checksum():
    encoded = encode_packet(make_packet())
    corrupted = corrupt_packet(encoded)

    original = json.loads(encoded)
    modified = json.loads(corrupted)

    assert original["checksum"] == modified["checksum"]


def test_corrupted_packet_rejected():
    encoded = encode_packet(make_packet())
    corrupted = corrupt_packet(encoded)

    with pytest.raises(PacketError, match="Checksum mismatch"):
        decode_packet(corrupted)


def test_corrupted_ack_rejected():
    encoded = encode_packet(make_packet("ACK"))
    corrupted = corrupt_packet(encoded)

    with pytest.raises(PacketError, match="Checksum mismatch"):
        decode_packet(corrupted)


def test_send_packet_without_corruption():
    sock = Mock()
    endpoint = ("127.0.0.1", 5000)
    packet = make_packet()

    send_packet(sock, endpoint, packet)

    data, destination = sock.sendto.call_args.args

    assert destination == endpoint
    assert decode_packet(data) == packet


def test_send_packet_with_corruption():
    sock = Mock()
    endpoint = ("127.0.0.1", 5000)

    send_packet(
        sock,
        endpoint,
        make_packet(),
        corrupt=True
    )

    data, destination = sock.sendto.call_args.args

    assert destination == endpoint

    with pytest.raises(PacketError, match="Checksum mismatch"):
        decode_packet(data)


def test_corrupt_rule_matches_once():
    rule = CorruptRule.parse("type=DATA,count=1")
    packet = make_packet()

    assert rule.check(packet, "routerA") is True
    assert rule.check(packet, "routerA") is False


def test_corrupt_rule_skips_first_packet():
    rule = CorruptRule.parse("type=DATA,skip=1,count=1")
    packet = make_packet()

    assert rule.check(packet, "routerA") is False
    assert rule.check(packet, "routerA") is True
    assert rule.check(packet, "routerA") is False


def test_corrupt_rule_ignores_nonmatching_packet():
    rule = CorruptRule.parse("type=DATA,count=1")

    assert rule.check(make_packet("ACK"), "routerA") is False
    assert rule.check(make_packet("DATA"), "routerA") is True


def test_corrupt_rule_matches_next_hop():
    rule = CorruptRule.parse(
        "type=DATA,next_hop=routerB,count=1"
    )

    packet = make_packet()

    assert rule.check(packet, "routerA") is False
    assert rule.check(packet, "routerB") is True
    assert rule.check(packet, "routerB") is False


def test_corruption_does_not_modify_original_packet():
    packet = make_packet()
    original = dict(packet)

    corrupt_packet(encode_packet(packet))

    assert packet == original