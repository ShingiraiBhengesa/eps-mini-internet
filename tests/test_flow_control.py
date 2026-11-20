
"""Tests for the receiver buffer and window advertisements."""

from src.flow_control import (
    FlowControlledSender,
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


class LinkedHost(FakeHost):
    """Fake host that forwards sent packets through an in-memory link."""

    def __init__(self, name, on_send=None):
        super().__init__()
        self.name = name
        self.on_send = on_send

    def _send(self, packet):
        super()._send(packet)

        if self.on_send is not None:
            self.on_send(packet)

    def handle(self, packet):
        return packet if packet["dst"] == self.name else None


class MemoryLink:
    """Connect a sender and receiver without real socket delays."""

    def __init__(
        self,
        count=5,
        buffer_size=1,
        consume_interval=0.15,
        drop_window_update=False,
    ):
        self.clock = Clock()
        self.ack_queue = []
        self.drop_window_update = drop_window_update
        self.dropped = False

        self.sender_host = LinkedHost(
            "host1",
            on_send=self.send_data,
        )
        self.sender_host.receive = self.receive_ack

        self.receiver_host = LinkedHost(
            "host2",
            on_send=self.send_ack,
        )

        self.receiver = WindowReceiver(
            self.receiver_host,
            buffer_size=buffer_size,
            consume_interval=consume_interval,
            expect=count,
            clock=self.clock,
        )

    def send_data(self, packet):
        self.receiver.on_data(packet)

    def send_ack(self, packet):
        advertised = parse_window_advertisement(packet["payload"])

        # Simulate losing the first update that reopens the window.
        if (
            self.drop_window_update
            and not self.dropped
            and advertised[0] > 0
            and self.receiver.summary()["delivered"] > 0
        ):
            self.dropped = True
            return

        self.ack_queue.append(packet)

    def receive_ack(self, timeout):
        if self.ack_queue:
            return self.ack_queue.pop(0)

        self.clock.advance(timeout)
        self.receiver.tick()

        return self.ack_queue.pop(0) if self.ack_queue else None

    def run(self):
        sender = FlowControlledSender(
            self.sender_host,
            "host2",
            self.receiver.expect,
            "test",
            window=4,
            timeout=0.25,
            retries=4,
            rate=50,
            clock=self.clock,
            sleep=self.clock.advance,
        )

        result = sender.run()

        # Allow the application to consume the remaining buffered data.
        self.clock.advance(
            self.receiver.expect * self.receiver.consume_interval + 0.1
        )
        self.receiver.tick()

        return result


def test_sender_pauses_and_resumes_when_buffer_is_full():
    network = MemoryLink()
    result = network.run()

    assert result["success"] is True
    assert result["zero_window_events"] >= 1
    assert result["max_in_flight"] <= 1
    assert network.receiver.summary()["complete"] is True

    delivered = [
        details["seq"]
        for event, details in network.receiver_host.events
        if event == "delivered"
    ]

    assert delivered == [1, 2, 3, 4, 5]
    assert not any(
        event == "receiver_buffer_full"
        for event, _ in network.receiver_host.events
    )


def test_lost_window_update_is_recovered_by_probe():
    network = MemoryLink(
        count=3,
        drop_window_update=True,
    )

    result = network.run()

    assert result["success"] is True
    assert network.dropped is True
    assert result["window_probes"] >= 1
    assert network.receiver.summary()["complete"] is True


def test_zero_window_failure_is_bounded():
    network = MemoryLink(
        count=2,
        consume_interval=1000,
    )

    sender = FlowControlledSender(
        network.sender_host,
        "host2",
        2,
        "test",
        window=4,
        timeout=0.1,
        retries=2,
        rate=50,
        max_window_probes=2,
        clock=network.clock,
        sleep=network.clock.advance,
    )

    result = sender.run()

    assert result["success"] is False
    assert result["failed_at"] == 2
    assert result["window_probes"] == 2


def test_flow_control_requires_go_back_n(capsys):
    """Reject flow control with incompatible transfer modes."""
    from src.reliable import main

    result = main([
        "send",
        "--name", "host1",
        "--to", "host2",
        "--mode", "stopwait",
        "--flow-control",
    ])

    assert result == 2
    assert "requires --mode gobackn" in capsys.readouterr().err


def test_invalid_receiver_buffer_size(capsys):
    """Reject receiver buffers that cannot hold any messages."""
    from src.reliable import main

    result = main([
        "receive",
        "--name", "host2",
        "--flow-control",
        "--buffer-size", "0",
    ])

    assert result == 2
    assert "buffer-size must be at least 1" in capsys.readouterr().err


def test_flow_control_through_real_routers(
    tmp_path, upper_config_on_free_ports
):
    """Verify flow control through the complete four-router network."""
    import json

    from scripts.launch import run_transfer
    from scripts.metrics import run_metrics

    run_dir, meta = run_transfer(
        upper_config_on_free_ports,
        "flow-control-e2e",
        10,
        tmp_path / "runs",
        mode="gobackn",
        count=5,
        rate=30,
        timeout=0.5,
        retries=5,
        window=4,
        linger=0.3,
        flow_control=True,
        buffer_size=1,
        consume_interval=0.1,
    )

    metrics = run_metrics(run_dir)

    assert meta["sender_exit_code"] == 0
    assert meta["receiver_exited_by_itself"] is True
    assert meta["flow_control"] is True

    assert metrics["delivered"] == 5
    assert metrics["full_sequence"] is True
    assert metrics["in_order"] is True
    assert metrics["sender_success"] is True

    def events(name):
        path = run_dir / f"{name}.jsonl"
        return [
            json.loads(line)
            for line in path.read_text().splitlines()
        ]

    sender_events = events("host1")
    receiver_events = events("host2")

    assert any(
        event["event"] == "window_updated"
        for event in sender_events
    )

    assert any(
        event["event"] == "window_advertised"
        for event in receiver_events
    )

    assert any(
        event["event"] == "delivered"
        for event in receiver_events
    )
