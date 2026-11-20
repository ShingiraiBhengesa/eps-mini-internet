
"""Tests for the receiver buffer and window advertisements."""

from src.flow_control import (
    WindowReceiver,
    parse_window_advertisement,
)
from src.reliable import message_id


class Clock:
    """Controllable clock for deterministic tests."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeHost:
    """Record packets and events without opening UDP sockets."""

    def __init__(self):
        self.name = "host2"
        self.sent = []
        self.events = []

    def log(self, device, event, packet=None, **details):
        self.events.append((event, details))

    def _make_packet(self, kind, dst, mid, payload):
        return {
            "src": self.name,
            "dst": dst,
            "hop_limit": 16,
            "type": kind,
            "message_id": mid,
            "payload": payload,
        }

    def _send(self, packet):
        self.sent.append(packet)


def data(seq):
    """Create a Go-Back-N DATA packet."""
    return {
        "src": "host1",
        "dst": "host2",
        "hop_limit": 16,
        "type": "DATA",
        "message_id": message_id("gobackn", "test", seq),
        "payload": f"data-{seq:04d}",
    }


def test_window_advertisement_validation():
    assert parse_window_advertisement(
        '{"rwnd":2,"version":3}'
    ) == (2, 3)

    assert parse_window_advertisement(
        '{"rwnd":-1,"version":3}'
    ) is None

    assert parse_window_advertisement(
        '{"rwnd":true,"version":3}'
    ) is None

    assert parse_window_advertisement("invalid") is None


def test_receiver_advertises_available_space():
    host = FakeHost()
    clock = Clock()

    receiver = WindowReceiver(
        host, buffer_size=2, consume_interval=0.1, clock=clock
    )

    receiver.on_data(data(1))
    receiver.on_data(data(2))

    windows = [
        parse_window_advertisement(p["payload"])[0]
        for p in host.sent
    ]

    assert windows == [1, 0]
    assert receiver.summary()["delivered"] == 0


def test_receiver_rejects_packet_when_buffer_full():
    host = FakeHost()
    receiver = WindowReceiver(host, buffer_size=2)

    receiver.on_data(data(1))
    receiver.on_data(data(2))
    receiver.on_data(data(3))

    events = [event for event, _ in host.events]

    assert "receiver_buffer_full" in events
    assert receiver.summary()["delivered"] == 0
    assert len(receiver.buffers[next(iter(receiver.buffers))]["pending"]) == 2


def test_receiver_reopens_window_after_consumption():
    host = FakeHost()
    clock = Clock()

    receiver = WindowReceiver(
        host,
        buffer_size=2,
        consume_interval=0.1,
        expect=3,
        clock=clock,
    )

    receiver.on_data(data(1))
    receiver.on_data(data(2))

    clock.advance(0.11)
    receiver.tick()

    assert receiver.summary()["delivered"] == 1
    assert parse_window_advertisement(
        host.sent[-1]["payload"]
    )[0] == 1

    receiver.on_data(data(3))

    clock.advance(0.21)
    receiver.tick()

    assert receiver.summary()["delivered"] == 3
    assert receiver.summary()["complete"] is True
