
import pytest

from src.packet import (
    MAX_ADDRESS_LENGTH,
    MAX_MESSAGE_ID_LENGTH,
    MAX_PACKET_SIZE,
    MESSAGE_TYPES,
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


def payload_for_size(size):
    """Return a packet whose serialized form is exactly size bytes."""
    packet = make_packet()
    packet["payload"] = ""
    packet["payload"] = "x" * (size - len(encode_packet(packet)))
    return packet


def test_accept_packet_at_size_limit():
    packet = payload_for_size(MAX_PACKET_SIZE)
    data = encode_packet(packet)

    assert len(data) == MAX_PACKET_SIZE
    assert decode_packet(data) == packet


def test_reject_packet_one_byte_over_limit():
    packet = payload_for_size(MAX_PACKET_SIZE + 1)

    with pytest.raises(PacketError):
        encode_packet(packet)


def test_reject_oversized_bytes_on_decode():
    with pytest.raises(PacketError):
        decode_packet(b"x" * (MAX_PACKET_SIZE + 1))


@pytest.mark.parametrize("message_type", sorted(MESSAGE_TYPES))
def test_accept_known_message_types(message_type):
    packet = make_packet()
    packet["type"] = message_type
    assert decode_packet(encode_packet(packet)) == packet


def test_reject_unknown_message_type():
    packet = make_packet()
    packet["type"] = "PING"

    with pytest.raises(PacketError):
        encode_packet(packet)


def test_reject_duplicate_keys():
    data = (
        b'{"src":"host1","dst":"host2","hop_limit":16,"type":"PROBE",'
        b'"message_id":"m1","payload":"","dst":"routerA"}'
    )

    with pytest.raises(PacketError):
        decode_packet(data)


def test_reject_unexpected_field():
    packet = make_packet()
    packet["extra"] = 1

    with pytest.raises(PacketError):
        encode_packet(packet)


@pytest.mark.parametrize("field", ["src", "dst"])
def test_reject_long_address(field):
    packet = make_packet()
    packet[field] = "h" * (MAX_ADDRESS_LENGTH + 1)

    with pytest.raises(PacketError):
        encode_packet(packet)


def test_reject_long_message_id():
    packet = make_packet()
    packet["message_id"] = "m" * (MAX_MESSAGE_ID_LENGTH + 1)

    with pytest.raises(PacketError):
        encode_packet(packet)


@pytest.mark.parametrize("field", ["src", "dst", "type", "message_id"])
def test_reject_empty_string_field(field):
    packet = make_packet()
    packet[field] = ""

    with pytest.raises(PacketError):
        encode_packet(packet)


def test_reject_non_string_payload():
    packet = make_packet()
    packet["payload"] = {"nested": True}

    with pytest.raises(PacketError):
        encode_packet(packet)


def test_reject_float_hop_limit():
    packet = make_packet()
    packet["hop_limit"] = 16.0

    with pytest.raises(PacketError):
        encode_packet(packet)


def test_reject_json_array():
    with pytest.raises(PacketError):
        decode_packet(b"[]")


def test_reject_invalid_utf8():
    with pytest.raises(PacketError):
        decode_packet(b"\xff\xfe")


def test_reject_non_bytes():
    with pytest.raises(PacketError):
        decode_packet("not bytes")
