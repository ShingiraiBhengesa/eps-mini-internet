
import pytest

from src.packet import (
    PacketError,
    encode_packet,
    decode_packet,
)


def make_packet():
    return {
        "src": "host1",
        "dst": "routerA",
        "hop_limit": 16,
        "type": "PROBE",
        "message_id": "test-001",
        "payload": "Hello!"
    }


def test_packet_round_trip():
    packet = make_packet()
    assert decode_packet(encode_packet(packet)) == packet


def test_reject_missing_field():
    packet = make_packet()
    del packet["message_id"]

    with pytest.raises(PacketError):
        encode_packet(packet)


def test_reject_invalid_hop_limit():
    packet = make_packet()
    packet["hop_limit"] = -1

    with pytest.raises(PacketError):
        encode_packet(packet)


def test_reject_boolean_hop_limit():
    packet = make_packet()
    packet["hop_limit"] = True

    with pytest.raises(PacketError):
        encode_packet(packet)


def test_reject_malformed_json():
    with pytest.raises(PacketError):
        decode_packet(b"{not valid json")


def test_reject_oversized_packet():
    packet = make_packet()
    packet["payload"] = "x" * 2000

    with pytest.raises(PacketError):
        encode_packet(packet)


def test_reject_empty_packet():
    with pytest.raises(PacketError):
        decode_packet(b"")
