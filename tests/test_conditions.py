
import pytest

from src.conditions import LinkScheduler


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


@pytest.fixture
def setup():
    config = {
        "devices": {
            "routerA": {"neighbors": ["routerB", "routerC"], "udp_host": "127.0.0.1", "udp_port": 5101},
            "routerB": {"neighbors": ["routerA"], "udp_host": "127.0.0.1", "udp_port": 5102},
            "routerC": {"neighbors": ["routerA"], "udp_host": "127.0.0.1", "udp_port": 5103},
        },
        "links": [
            {"endpoints": ["routerA", "routerB"], "loss": 0.0, "delay_ms": 0},
            {"endpoints": ["routerA", "routerC"], "loss": 0.0, "delay_ms": 0},
        ],
    }

    packet = {
        "src": "host1",
        "dst": "host2",
        "hop_limit": 10,
        "type": "PROBE",
        "message_id": "m1",
        "payload": "hello",
    }

    clock = FakeClock()
    sent = []
    events = []

    def sender(sock, endpoint, value):
        sent.append((endpoint, value))

    def logger(device, event, packet=None, **details):
        events.append(event)

    return config, packet, clock, sent, events, sender, logger


def build(setup, seed=7):
    config, _, clock, _, _, sender, logger = setup
    return LinkScheduler(
        config, "routerA", object(),
        seed=seed, log=logger, clock=clock, sender=sender
    )


def test_zero_loss_sends_immediately(setup):
    _, packet, _, sent, events, _, _ = setup
    scheduler = build(setup)

    assert scheduler.enqueue("routerB", packet)
    assert scheduler.send_ready() == 1
    assert sent == [(("127.0.0.1", 5102), packet)]
    assert "sent" in events


def test_full_loss_drops_every_packet(setup):
    config, packet, _, sent, events, _, _ = setup
    config["links"][0]["loss"] = 1.0
    scheduler = build(setup)

    assert all(not scheduler.enqueue("routerB", packet) for _ in range(10))
    assert scheduler.send_ready() == 0
    assert not sent
    assert events.count("drop_injected") == 10


def test_delay_does_not_block_other_link(setup):
    config, packet, clock, sent, _, _, _ = setup
    config["links"][0]["delay_ms"] = 500
    scheduler = build(setup)

    scheduler.enqueue("routerB", packet)
    scheduler.enqueue("routerC", packet)

    assert scheduler.send_ready() == 1
    assert sent[0][0] == ("127.0.0.1", 5103)

    clock.advance(0.5)

    assert scheduler.send_ready() == 1
    assert sent[1][0] == ("127.0.0.1", 5102)


def test_loss_is_reproducible_with_seed(setup):
    config, packet, _, _, _, _, _ = setup
    config["links"][0]["loss"] = 0.05

    first = build(setup, seed=19)
    second = build(setup, seed=19)

    results_a = [first.enqueue("routerB", packet) for _ in range(200)]
    results_b = [second.enqueue("routerB", packet) for _ in range(200)]

    assert results_a == results_b
    assert any(not delivered for delivered in results_a)


def test_unconfigured_neighbor_is_rejected(setup):
    _, packet, _, _, _, _, _ = setup
    scheduler = build(setup)

    with pytest.raises(ValueError, match="not a neighbor"):
        scheduler.enqueue("host2", packet)


def test_queued_packet_is_copied(setup):
    _, packet, _, sent, _, _, _ = setup
    scheduler = build(setup)

    scheduler.enqueue("routerB", packet)
    packet["payload"] = "changed"
    scheduler.send_ready()

    assert sent[0][1]["payload"] == "hello"
