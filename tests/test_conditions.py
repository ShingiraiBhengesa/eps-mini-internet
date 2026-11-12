
import pytest

from src.conditions import DropRule, LinkScheduler


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


def build(setup, seed=7, drop_rules=None):
    config, _, clock, _, _, sender, logger = setup
    return LinkScheduler(
        config, "routerA", object(),
        seed=seed, log=logger, clock=clock, sender=sender,
        drop_rules=drop_rules
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


def with_id(packet, message_id, packet_type="PROBE"):
    return dict(packet, message_id=message_id, type=packet_type)


def test_drop_rule_drops_matching_packet_once(setup):
    _, packet, _, sent, events, _, _ = setup
    scheduler = build(setup, drop_rules=[DropRule.parse("message_id=m2")])

    assert scheduler.enqueue("routerB", with_id(packet, "m1"))
    assert not scheduler.enqueue("routerB", with_id(packet, "m2"))
    # A retransmission keeps the same ID and must get through.
    assert scheduler.enqueue("routerB", with_id(packet, "m2"))

    scheduler.send_ready()
    assert [value["message_id"] for _, value in sent] == ["m1", "m2"]
    assert events.count("drop_forced") == 1
    assert "drop_injected" not in events


def test_drop_rule_skip_and_count(setup):
    _, packet, _, _, _, _, _ = setup
    rule = DropRule.parse("type=PROBE,skip=2,count=2")
    scheduler = build(setup, drop_rules=[rule])

    results = [
        scheduler.enqueue("routerB", with_id(packet, f"m{i}"))
        for i in range(1, 7)
    ]

    assert results == [True, True, False, False, True, True]


def test_drop_rule_matches_type_and_link(setup):
    _, packet, _, _, _, _, _ = setup
    rule = DropRule.parse("type=ACK,next_hop=routerC")
    scheduler = build(setup, drop_rules=[rule])

    assert scheduler.enqueue("routerC", with_id(packet, "a1", "DATA"))
    assert scheduler.enqueue("routerB", with_id(packet, "a1", "ACK"))
    assert not scheduler.enqueue("routerC", with_id(packet, "a1", "ACK"))


def test_forced_drop_is_logged_separately_from_random_loss(setup):
    config, packet, _, _, _, _, _ = setup
    config["links"][0]["loss"] = 1.0
    logged = []

    def logger(device, event, packet=None, **details):
        logged.append((event, details))

    scheduler = LinkScheduler(
        config, "routerA", object(), seed=1, log=logger,
        clock=lambda: 0.0, sender=lambda *args: None,
        drop_rules=[DropRule.parse("message_id=m1")]
    )

    scheduler.enqueue("routerB", with_id(packet, "m1"))
    scheduler.enqueue("routerB", with_id(packet, "m2"))

    assert logged[0] == (
        "drop_forced",
        {"next_hop": "routerB", "rule": "message_id=m1,skip=0,count=1"},
    )
    assert logged[1][0] == "drop_injected"


@pytest.mark.parametrize("text", [
    "",
    "message_id",
    "colour=red",
    "skip=1",
    "type=PROBE,count=0",
    "type=PROBE,skip=-1",
    "type=PROBE,count=two",
    "type=PROBE,type=ECHO",
])
def test_bad_drop_rules_are_rejected(text):
    with pytest.raises(ValueError):
        DropRule.parse(text)


def test_link_settings_lists_each_neighbor(setup):
    config, _, _, _, _, _, _ = setup
    config["links"][1]["loss"] = 0.05
    config["links"][1]["delay_ms"] = 20

    assert build(setup).link_settings() == [
        {"neighbor": "routerB", "loss": 0.0, "delay_ms": 0},
        {"neighbor": "routerC", "loss": 0.05, "delay_ms": 20},
    ]
