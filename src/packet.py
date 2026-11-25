import json
import zlib

MAX_PACKET_SIZE = 1200
MAX_ADDRESS_LENGTH = 32
MAX_MESSAGE_ID_LENGTH = 64

REQUIRED_FIELDS = {
    "src",
    "dst",
    "hop_limit",
    "type",
    "message_id",
    "payload",
}

# PROBE/ECHO for reachability tests, DATA/ACK for Track B reliable transfer.
MESSAGE_TYPES = {"PROBE", "ECHO", "DATA", "ACK"}


class PacketError(ValueError):
    """Raised when a packet is invalid."""


def calculate_checksum(packet: dict) -> int:
    """Calculate a CRC32 checksum using the packet contents."""
    return zlib.crc32(_serialize(packet)) & 0xFFFFFFFF


def encode_packet(packet: dict) -> bytes:
    """Validate, attach a checksum, and serialize a packet."""
    validate_packet(packet)

    # Create a copy so the original packet is not modified.
    packet_with_checksum = dict(packet)

    # Calculate and attach the checksum before transmission.
    packet_with_checksum["checksum"] = calculate_checksum(packet)

    data = _serialize(packet_with_checksum)

    # Ensure the packet still meets the size limit with the checksum.
    if len(data) > MAX_PACKET_SIZE:
        raise PacketError("Packet with checksum exceeds 1200 bytes")

    return data


def decode_packet(data: bytes) -> dict:
    """Decode, verify checksum, and validate bytes received over UDP."""
    if not isinstance(data, bytes):
        raise PacketError("Packet must be bytes")

    if not data or len(data) > MAX_PACKET_SIZE:
        raise PacketError("Empty or oversized packet")

    try:
        packet = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PacketError("Invalid JSON packet") from exc

    if not isinstance(packet, dict):
        raise PacketError("Packet must be a JSON object")

    # Extract and remove the checksum from the received packet.
    received_checksum = packet.pop("checksum", None)

    if (
        type(received_checksum) is not int
        or not 0 <= received_checksum <= 0xFFFFFFFF
    ):
        raise PacketError("Missing or invalid checksum")

    # Recalculate the checksum using the received packet contents.
    expected_checksum = calculate_checksum(packet)

    # Reject the packet if corruption is detected.
    if received_checksum != expected_checksum:
        raise PacketError("Checksum mismatch: corrupted packet")

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

    for field in ("src", "dst"):
        if len(packet[field]) > MAX_ADDRESS_LENGTH:
            raise PacketError(
                f"{field} exceeds {MAX_ADDRESS_LENGTH} characters"
            )

    if len(packet["message_id"]) > MAX_MESSAGE_ID_LENGTH:
        raise PacketError(
            f"message_id exceeds {MAX_MESSAGE_ID_LENGTH} characters"
        )

    if packet["type"] not in MESSAGE_TYPES:
        raise PacketError(f"Unknown message type {packet['type']!r}")

    hop_limit = packet["hop_limit"]
    if type(hop_limit) is not int or not 0 <= hop_limit <= 255:
        raise PacketError("hop_limit must be an integer from 0 to 255")

    if not isinstance(packet["payload"], str):
        raise PacketError("payload must be a string")

    # Check the serialized size as well as the received byte size.
    if len(_serialize(packet)) > MAX_PACKET_SIZE:
        raise PacketError("Serialized packet exceeds 1200 bytes")


def _serialize(packet: dict) -> bytes:
    try:
        return json.dumps(
            packet,
            separators=(",", ":"),
            ensure_ascii=True,
            # Ensure consistent field ordering for checksum calculation.
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PacketError("Packet cannot be serialized") from exc


def _reject_duplicate_keys(pairs):
    packet = {}
    for key, value in pairs:
        if key in packet:
            raise PacketError(f"Duplicate field {key!r}")
        packet[key] = value
    return packet
