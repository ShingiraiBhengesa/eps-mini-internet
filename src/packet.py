
import json

MAX_PACKET_SIZE = 1200

REQUIRED_FIELDS = {
    "src",
    "dst",
    "hop_limit",
    "type",
    "message_id",
    "payload",
}


class PacketError(ValueError):
    """Raised when a packet is invalid."""


def encode_packet(packet: dict) -> bytes:
    """Validate and serialize a packet."""
    validate_packet(packet)

    try:
        data = json.dumps(
            packet,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PacketError("Packet cannot be serialized") from exc

    if len(data) > MAX_PACKET_SIZE:
        raise PacketError("Serialized packet exceeds 1200 bytes")

    return data


def decode_packet(data: bytes) -> dict:
    """Decode and validate bytes received over UDP."""
    if not isinstance(data, bytes):
        raise PacketError("Packet must be bytes")

    if not data or len(data) > MAX_PACKET_SIZE:
        raise PacketError("Empty or oversized packet")

    try:
        packet = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PacketError("Invalid JSON packet") from exc

    validate_packet(packet)
    return packet


def validate_packet(packet: dict) -> None:
    """Check required fields and their types."""
    if not isinstance(packet, dict):
        raise PacketError("Packet must be a JSON object")

    if set(packet) != REQUIRED_FIELDS:
        raise PacketError("Packet has missing or unexpected fields")

    for field in ("src", "dst", "type", "message_id"):
        value = packet[field]
        if not isinstance(value, str) or not value:
            raise PacketError(f"{field} must be a nonempty string")

    hop_limit = packet["hop_limit"]
    if type(hop_limit) is not int or not 0 <= hop_limit <= 255:
        raise PacketError("hop_limit must be an integer from 0 to 255")

    if not isinstance(packet["payload"], str):
        raise PacketError("payload must be a string")

    # Check the serialized size as well as the received byte size.
    try:
        size = len(json.dumps(
            packet,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise PacketError("Packet cannot be serialized") from exc

    if size > MAX_PACKET_SIZE:
        raise PacketError("Serialized packet exceeds 1200 bytes")
