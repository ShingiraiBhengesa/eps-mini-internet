import pytest
import json

from src.packet import (
    MAX_ADDRESS_LENGTH,
    MAX_MESSAGE_ID_LENGTH,
    MAX_PACKET_SIZE,
    MESSAGE_TYPES,
    PacketError,
    calculate_checksum,
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

    base_size = len(encode_packet(packet))

    # Account for the variable length of the CRC32 checksum.
    for length in range(
        max(0, size - base_size - 10),
        size - base_size + 11
    ):
        for suffix in "abcdefghijklmnopqrstuvwxyz0123456789":
            if length == 0:
                packet["payload"] = ""
            else:
                packet["payload"] = "x" * (length - 1) + suffix

            wire_packet = dict(packet)
            wire_packet["checksum"] = calculate_checksum(packet)

            data = json.dumps(
                wire_packet,
                separators=(",", ":"),
                ensure_ascii=True,
                sort_keys=True,
            ).encode("utf-8")

            if len(data) == size:
                return packet

    raise AssertionError(f"Could not construct packet of size {size}")


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


# --------------------------------------------------
# Checksum-based corruption detection tests
# --------------------------------------------------


def test_checksum_added():
    """Verify that encoding adds a checksum."""
    packet = make_packet()
    encoded = json.loads(encode_packet(packet))

    assert "checksum" in encoded
    assert encoded["checksum"] == calculate_checksum(packet)


def test_checksum_deterministic():
    """Identical packets should produce identical checksums."""
    packet = make_packet()

    assert calculate_checksum(packet) == calculate_checksum(dict(packet))


def test_checksum_field_order():
    """Dictionary field ordering should not affect the checksum."""
    packet = make_packet()
    reordered = dict(reversed(list(packet.items())))

    assert calculate_checksum(packet) == calculate_checksum(reordered)


def test_checksum_does_not_modify_original():
    """Encoding should not add a checksum to the original dictionary."""
    packet = make_packet()
    original = dict(packet)

    encode_packet(packet)

    assert packet == original
    assert "checksum" not in packet


def test_checksum_removed_after_decode():
    """The decoded packet should not contain the checksum field."""
    packet = make_packet()
    decoded = decode_packet(encode_packet(packet))

    assert "checksum" not in decoded
    assert decoded == packet


def test_reject_corrupted_payload():
    """Detect a payload changed during transmission."""
    packet = make_packet()
    encoded = encode_packet(packet)

    corrupted = encoded.replace(b"Hello!", b"Jello!")

    assert corrupted != encoded

    with pytest.raises(PacketError, match="Checksum mismatch"):
        decode_packet(corrupted)


@pytest.mark.parametrize("field, value", [
    ("src", "host2"),
    ("dst", "routerB"),
    ("hop_limit", 15),
    ("type", "DATA"),
    ("message_id", "test-002"),
])
def test_reject_corrupted_header(field, value):
    """Detect corruption in any packet header field."""
    packet = json.loads(encode_packet(make_packet()))
    packet[field] = value

    corrupted = json.dumps(packet).encode("utf-8")

    with pytest.raises(PacketError, match="Checksum mismatch"):
        decode_packet(corrupted)


def test_reject_corrupted_checksum():
    """Detect corruption of the checksum itself."""
    packet = json.loads(encode_packet(make_packet()))

    # Flip one bit in the checksum.
    packet["checksum"] ^= 1

    corrupted = json.dumps(packet).encode("utf-8")

    with pytest.raises(PacketError, match="Checksum mismatch"):
        decode_packet(corrupted)


def test_reject_missing_checksum():
    """Reject a packet transmitted without a checksum."""
    packet = json.loads(encode_packet(make_packet()))
    del packet["checksum"]

    data = json.dumps(packet).encode("utf-8")

    with pytest.raises(PacketError, match="Missing or invalid checksum"):
        decode_packet(data)


@pytest.mark.parametrize("checksum", [
    None,
    True,
    "invalid",
    -1,
    1.5,
    0x100000000,
])
def test_reject_invalid_checksum(checksum):
    """Reject checksums with invalid types or values."""
    packet = json.loads(encode_packet(make_packet()))
    packet["checksum"] = checksum

    data = json.dumps(packet).encode("utf-8")

    with pytest.raises(PacketError, match="Missing or invalid checksum"):
        decode_packet(data)


def test_checksum_after_forwarding():
    """Verify checksum recalculation when a router forwards a packet."""
    packet = make_packet()

    received = decode_packet(encode_packet(packet))

    # Simulate a router decrementing the hop limit.
    received["hop_limit"] -= 1

    forwarded = encode_packet(received)
    decoded = decode_packet(forwarded)

    assert decoded["hop_limit"] == 15
    assert decoded == received


@pytest.mark.parametrize("message_type", sorted(MESSAGE_TYPES))
def test_checksum_corruption_all_message_types(message_type):
    """Verify corruption detection for every supported message type."""
    packet = make_packet()
    packet["type"] = message_type

    encoded = encode_packet(packet)
    corrupted = encoded.replace(b"Hello!", b"Jello!")

    with pytest.raises(PacketError, match="Checksum mismatch"):
        decode_packet(corrupted)