
import socket
import threading

from src.packet import decode_packet
from src.transport import create_udp_socket, send_packet


def test_neighbor_udp_communication():
    receiver = create_udp_socket("127.0.0.1", 0)
    receiver.settimeout(2)

    receiver_address = receiver.getsockname()
    sender = create_udp_socket("127.0.0.1", 0)

    packet = {
        "src": "host1",
        "dst": "routerA",
        "hop_limit": 16,
        "type": "PROBE",
        "message_id": "test-001",
        "payload": "Hello, Router A!"
    }

    try:
        send_packet(sender, receiver_address, packet)

        data, sender_address = receiver.recvfrom(1201)
        received_packet = decode_packet(data)

        assert received_packet == packet
        assert sender_address == sender.getsockname()
    finally:
        sender.close()
        receiver.close()
