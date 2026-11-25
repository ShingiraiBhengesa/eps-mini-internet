import socket
import json

from .packet import (
    MAX_PACKET_SIZE,
    PacketError,
    decode_packet,
    encode_packet,
)


def corrupt_packet(data):
    """Modify a serialized packet without recalculating its checksum."""
    packet = json.loads(data.decode("utf-8"))

    # Corrupt the payload while preserving the original checksum.
    payload = packet["payload"]

    if payload:
        replacement = "X" if payload[0] != "X" else "Y"
        packet["payload"] = replacement + payload[1:]
    else:
        # ACK packets normally have an empty payload.
        packet["payload"] = "X"

    return json.dumps(
        packet,
        separators=(",", ":"),
        ensure_ascii=True,
        sort_keys=True,
    ).encode("utf-8")


def send_packet(sock, endpoint, packet, corrupt=False):
    """Send a validated packet to a configured neighbor."""
    data = encode_packet(packet)

    # Inject corruption AFTER the checksum has been calculated.
    if corrupt:
        data = corrupt_packet(data)

    sock.sendto(data, endpoint)


def receive_packet(sock):
    """Receive one datagram and decode it."""
    data, sender = sock.recvfrom(MAX_PACKET_SIZE + 1)

    if len(data) > MAX_PACKET_SIZE:
        raise PacketError("Received oversized UDP datagram")

    packet = decode_packet(data)
    return packet, sender


def create_udp_socket(host, port):
    """Create a UDP socket bound to this device's endpoint."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((host, port))
    return sock
