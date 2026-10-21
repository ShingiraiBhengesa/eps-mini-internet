
import socket

from .packet import (
    MAX_PACKET_SIZE,
    PacketError,
    decode_packet,
    encode_packet,
)


def send_packet(sock, endpoint, packet):
    """Send a validated packet to a configured neighbor."""
    data = encode_packet(packet)
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
