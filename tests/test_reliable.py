
import threading
import time

import pytest

from scripts.make_routes import make_route_config
from src.conditions import DropRule
from src.config import load_config
from src.host import Host
from src.packet import decode_packet
from src.reliable import (
    BestEffortSender,
    GoBackNSender,
    Pacer,
    Receiver,
    StopAndWaitSender,
    data_payload,
    main,
    message_id,
    parse_message_id,
)
from src.router import Router
from src.transport import create_udp_socket, send_packet


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class FakeHost:
    """Just enough of a Host for the receiver: records logs and sent packets."""

    def __init__(self, name="host2", on_send=None):
        self.name = name
        self.events = []
        self.sent = []
        self.on_send = on_send

    def log(self, device, event, packet=None, **details):
        self.events.append((event, details))

    def _make_packet(self, kind, dst, mid, payload):
        return {"src": self.name, "dst": dst, "hop_limit": 16,
                "type": kind, "message_id": mid, "payload": payload}

    def _send(self, packet):
        self.sent.append(packet)
        if self.on_send:
            self.on_send(packet)

    def names(self):
        return [event for event, _ in self.events]


def data(seq, transfer="t", mode="stopwait", src="host1"):
    return {"src": src, "dst": "host2", "hop_limit": 13, "type": "DATA",
            "message_id": message_id(mode, transfer, seq), "payload": data_payload(seq)}


def delivered(host):
    return [d["seq"] for e, d in host.events if e == "delivered"]


# ---------------------------------------------------------------- message IDs

def test_message_ids_round_trip():
    assert message_id("stopwait", "ev-final-ack", 10) == "sw-ev-final-ack-10"
    assert parse_message_id("sw-ev-final-ack-10") == ("stopwait", "ev-final-ack", 10)
    assert parse_message_id("be-ab12cd-3") == ("besteffort", "ab12cd", 3)


@pytest.mark.parametrize("text", ["host1-ab12-1", "sw-t-0", "sw-t-x", "sw--1", "sw-t", "zz-t-1"])
def test_other_ids_are_not_track_b(text):
    assert parse_message_id(text) is None


# ---------------------------------------------------------------- receiver

def test_receiver_delivers_in_order_and_acks_each():
    host = FakeHost()
    receiver = Receiver(host)

    for seq in (1, 2, 3):
        receiver.on_data(data(seq))

    assert delivered(host) == [1, 2, 3]
    assert [p["message_id"] for p in host.sent] == ["sw-t-1", "sw-t-2", "sw-t-3"]
    assert all(p["type"] == "ACK" and p["dst"] == "host1" for p in host.sent)


def test_receiver_acks_a_duplicate_again_without_delivering_it():
    host = FakeHost()
    receiver = Receiver(host)

    receiver.on_data(data(1))
    receiver.on_data(data(1))

    assert delivered(host) == [1]
    assert host.names().count("duplicate") == 1
    assert [p["message_id"] for p in host.sent] == ["sw-t-1", "sw-t-1"]


def test_receiver_ignores_a_message_from_further_ahead():
    host = FakeHost()
    receiver = Receiver(host)

    receiver.on_data(data(2))
    assert delivered(host) == []
    assert host.sent == []
    assert "dropped_out_of_order" in host.names()

    receiver.on_data(data(1))
    receiver.on_data(data(2))
    assert delivered(host) == [1, 2]


def test_best_effort_receiver_never_acks_and_counts_unique_messages():
    host = FakeHost()
    receiver = Receiver(host, expect=4)

    for seq in (1, 2, 4, 4):
        receiver.on_data(data(seq, mode="besteffort"))

    assert host.sent == []
    assert delivered(host) == [1, 2, 4]
    summary = receiver.summary()
    assert summary["delivered"] == 3
    assert summary["duplicates"] == 1
    assert summary["complete"] is False
    assert summary["missing"] == [3]
    assert summary["in_order"] is True


def test_transfers_are_tracked_separately():
    host = FakeHost()
    receiver = Receiver(host)

    receiver.on_data(data(1, transfer="first"))
    receiver.on_data(data(1, transfer="second"))

    assert delivered(host) == [1, 1]
    assert receiver.summary()["transfer"] == "second"


def test_bad_data_id_is_dropped_without_ack():
    host = FakeHost()
    Receiver(host).on_data(dict(data(1), message_id="not-a-track-b-id-x"))

    assert host.sent == []
    assert host.names() == ["dropped_bad_data_id"]


def test_receiver_lingers_after_the_last_message_then_finishes():
    host, clock = FakeHost(), Clock()
    receiver = Receiver(host, expect=2, linger=3.0, clock=clock)

    receiver.on_data(data(1))
    receiver.on_data(data(2))
    assert "transfer_received" in host.names()

    clock.now += 2.0
    assert receiver.finished() is None
    receiver.on_data(data(2))             # the final message, resent
    assert host.sent[-1]["message_id"] == "sw-t-2"

    clock.now += 1.0
    assert receiver.finished() == "linger_over"
    assert receiver.summary()["complete"] is True


def test_receiver_observation_window():
    host, clock = FakeHost(), Clock()
    receiver = Receiver(host, expect=10, observe=5.0, clock=clock)

    assert receiver.finished() is None        # nothing has arrived yet
    receiver.on_data(data(1, mode="besteffort"))
    clock.now += 4.9
    assert receiver.finished() is None
    clock.now += 0.1
    assert receiver.finished() == "observation_over"


# ---------------------------------------------------------------- pacing

def test_pacer_keeps_sends_apart():
    clock, sleeps = Clock(), []

    def sleep(seconds):
        sleeps.append(seconds)
        clock.now += seconds

    pacer = Pacer(rate=10, clock=clock, sleep=sleep)
    times = []
    for _ in range(3):
        pacer.wait()
        times.append(clock.now)

    assert times == pytest.approx([100.0, 100.1, 100.2])


# ---------------------------------------------------------------- senders on real sockets

@pytest.fixture
def link():
    """host1 plus a socket standing in for routerA (the whole network)."""
    config = load_config("config/topology.json")
    sockets = {}
    for name in ("host1", "routerA"):
        sock = create_udp_socket("127.0.0.1", 0)
        sockets[name] = sock
        config["devices"][name]["udp_port"] = sock.getsockname()[1]

    events = []

    def log(device, event, packet=None, **details):
        if packet is not None:
            details = dict(details, message_id=packet["message_id"], type=packet["type"])
        events.append((event, details))

    host1 = Host(config, "host1", sock=sockets["host1"], log=log)
    yield {"host1": host1, "gateway": sockets["routerA"], "events": events}
    for sock in sockets.values():
        sock.close()


class FakeNetwork:
    """Plays the routers and host2: answers host1's DATA through the gateway.

    drop_data / drop_ack hold (message_id, n) pairs: the n-th copy of that
    DATA or ACK is lost. extra_acks sends each ACK this many extra times.
    """

    def __init__(self, link, drop_data=(), drop_ack=(), extra_acks=0, ack_delay=0.0):
        self.gateway = link["gateway"]
        self.host1 = link["host1"].sock.getsockname()
        self.drop_data, self.drop_ack = set(drop_data), set(drop_ack)
        self.extra_acks = extra_acks
        self.ack_delay = ack_delay
        self.seen_data, self.seen_ack = {}, {}
        self.remote = FakeHost(on_send=self.forward_ack)
        self.receiver = Receiver(self.remote)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run)

    def forward_ack(self, ack):
        mid = ack["message_id"]
        self.seen_ack[mid] = self.seen_ack.get(mid, 0) + 1
        if (mid, self.seen_ack[mid]) in self.drop_ack:
            return
        time.sleep(self.ack_delay)
        for _ in range(1 + self.extra_acks):
            send_packet(self.gateway, self.host1, ack)

    def run(self):
        self.gateway.settimeout(0.05)
        while not self.stop.is_set():
            try:
                raw, _ = self.gateway.recvfrom(1201)
            except OSError:
                continue
            packet = decode_packet(raw)
            mid = packet["message_id"]
            self.seen_data[mid] = self.seen_data.get(mid, 0) + 1
            if (mid, self.seen_data[mid]) not in self.drop_data:
                self.receiver.on_data(dict(packet, hop_limit=13))

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(timeout=2)


def sender(link, count=5, transfer="t", timeout=0.2, retries=3, rate=200):
    return StopAndWaitSender(link["host1"], "host2", count, transfer,
                             timeout=timeout, retries=retries, rate=rate)


def test_stop_and_wait_without_loss(link):
    with FakeNetwork(link) as net:
        summary = sender(link).run()

    assert summary["success"] is True
    assert (summary["acked"], summary["retransmissions"]) == (5, 0)
    assert delivered(net.remote) == [1, 2, 3, 4, 5]


def test_dropped_data_is_resent_and_recovered(link):
    with FakeNetwork(link, drop_data=[("sw-t-3", 1)]) as net:
        summary = sender(link).run()

    assert summary["success"] is True
    assert summary["retransmissions"] == 1
    assert delivered(net.remote) == [1, 2, 3, 4, 5]
    assert net.receiver.summary()["duplicates"] == 0
    retransmits = [d for e, d in link["events"] if e == "retransmit"]
    assert [(d["seq"], d["attempt"]) for d in retransmits] == [(3, 2)]


def test_lost_final_ack_is_recovered_by_the_receiver_acking_again(link):
    with FakeNetwork(link, drop_ack=[("sw-t-5", 1)]) as net:
        summary = sender(link).run()

    assert summary["success"] is True
    assert summary["retransmissions"] == 1
    assert delivered(net.remote) == [1, 2, 3, 4, 5]
    assert net.receiver.summary()["duplicates"] == 1
    assert [p["message_id"] for p in net.remote.sent].count("sw-t-5") == 2


def test_sender_gives_up_after_its_retry_limit_in_bounded_time(link):
    started = time.monotonic()
    summary = sender(link, timeout=0.1, retries=2).run()   # nobody answers
    took = time.monotonic() - started

    assert summary["success"] is False
    assert summary["failed_at"] == 1
    assert (summary["acked"], summary["retransmissions"]) == (0, 2)
    assert 0.3 <= took < 0.8                                 # 3 attempts x 0.1 s
    assert [e for e, _ in link["events"]][-2:] == ["transfer_failed", "transfer_done"]


def test_late_and_duplicate_acks_are_ignored(link):
    with FakeNetwork(link, extra_acks=1) as net:
        summary = sender(link).run()

    ignored = [d["reason"] for e, d in link["events"] if e == "ack_ignored"]
    assert summary["success"] is True
    assert summary["retransmissions"] == 0
    assert ignored and set(ignored) == {"a late or duplicate ACK for an earlier message"}
    assert delivered(net.remote) == [1, 2, 3, 4, 5]


def test_best_effort_sends_every_message_once_without_waiting(link):
    with FakeNetwork(link) as net:
        summary = BestEffortSender(link["host1"], "host2", 5, "t", rate=200).run()
        time.sleep(0.2)

    assert summary["retransmissions"] == 0 and summary["success"] is None
    assert net.remote.sent == []                               # no ACKs in best-effort
    assert delivered(net.remote) == [1, 2, 3, 4, 5]
    assert "ack_received" not in [e for e, _ in link["events"]]


# ---------------------------------------------------------------- through real routers

def test_stop_and_wait_through_routers_with_forced_drops():
    config = make_route_config(load_config("config/topology.json"), "upper")
    sockets = {name: create_udp_socket("127.0.0.1", 0) for name in config["devices"]}
    for name, sock in sockets.items():
        config["devices"][name]["udp_port"] = sock.getsockname()[1]

    events = []

    def log(device, event, packet=None, **details):
        events.append((device, event, details))

    rules = {
        "routerB": [DropRule.parse("type=DATA,message_id=sw-it-3")],   # lose DATA 3 once
        "routerD": [DropRule.parse("type=ACK,message_id=sw-it-5")],    # lose the final ACK once
    }
    routers = [Router(config, name, sock=sockets[name], log=log, drop_rules=rules.get(name))
               for name in ("routerA", "routerB", "routerC", "routerD")]
    host1 = Host(config, "host1", sock=sockets["host1"], log=log)
    host2 = Host(config, "host2", sock=sockets["host2"], log=log)

    stop = threading.Event()
    threads = [threading.Thread(target=r.serve, args=(stop, 0.02)) for r in routers]
    threads.append(threading.Thread(target=host2.serve, args=(stop, 0.02)))
    for thread in threads:
        thread.start()

    try:
        summary = StopAndWaitSender(host1, "host2", 5, "it", timeout=0.3, retries=3, rate=100).run()
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=2)
        for sock in sockets.values():
            sock.close()

    assert summary["success"] is True
    assert summary["retransmissions"] == 2
    assert [d["seq"] for dev, e, d in events if e == "delivered"] == [1, 2, 3, 4, 5]
    assert [d["seq"] for dev, e, d in events if e == "duplicate"] == [5]
    assert sorted(dev for dev, e, d in events if e == "drop_forced") == ["routerB", "routerD"]


# ---------------------------------------------------------------- command line

def test_cli_rejects_unknown_destination(capsys):
    assert main(["send", "--name", "host1", "--to", "hostZ"]) == 2
    assert "unknown destination" in capsys.readouterr().err


@pytest.mark.parametrize("args", [
    ["send", "--name", "host1", "--to", "host2", "--count", "0"],
    ["send", "--name", "host1", "--to", "host2", "--retries", "-1"],
    ["receive", "--name", "host2", "--expect", "0"],
    ["receive", "--name", "host2", "--linger", "-1"],
])
def test_cli_rejects_bad_numbers(args, capsys):
    assert main(args) == 2
    assert "error" in capsys.readouterr().err


@pytest.mark.parametrize("args", [
    ["send", "--name", "host1", "--to", "host2", "--transfer-id", "bad id"],
    ["send", "--name", "host1", "--to", "host2", "--rate", "0"],
    ["send", "--name", "host1", "--to", "host2", "--mode", "fast"],
])
def test_cli_rejects_bad_options(args):
    with pytest.raises(SystemExit):
        main(args)


# ---------------------------------------------------------------- Go-Back-N (enhancement)

def gbn(seq, transfer="t"):
    return data(seq, transfer=transfer, mode="gobackn")


def acks(host):
    return [p["message_id"] for p in host.sent]


def test_go_back_n_receiver_acks_cumulatively():
    host = FakeHost()
    receiver = Receiver(host)

    for seq in (1, 2, 3):
        receiver.on_data(gbn(seq))

    assert delivered(host) == [1, 2, 3]
    assert acks(host) == ["gbn-t-1", "gbn-t-2", "gbn-t-3"]


def test_go_back_n_receiver_discards_gaps_and_reacks_the_highest():
    host = FakeHost()
    receiver = Receiver(host)

    receiver.on_data(gbn(1))
    receiver.on_data(gbn(3))      # 2 was lost: discard 3, ACK 1 again
    receiver.on_data(gbn(4))
    receiver.on_data(gbn(2))
    receiver.on_data(gbn(1))      # an old copy: ACK 2 again

    assert delivered(host) == [1, 2]
    assert acks(host) == ["gbn-t-1", "gbn-t-1", "gbn-t-1", "gbn-t-2", "gbn-t-2"]
    assert host.names().count("dropped_out_of_order") == 2
    assert receiver.summary()["duplicates"] == 1


def test_go_back_n_receiver_acks_nothing_before_message_1():
    host = FakeHost()
    Receiver(host).on_data(gbn(2))

    assert host.sent == []
    assert delivered(host) == []


def go_back_n(link, count=8, window=4, timeout=0.2, retries=3, rate=200):
    return GoBackNSender(link["host1"], "host2", count, "t", window=window,
                         timeout=timeout, retries=retries, rate=rate)


def in_flight(events):
    """Most messages sent but not yet acknowledged, from the sender's log."""
    highest_sent = acked = most = 0
    for event, d in events:
        if event == "sent" and d.get("type") == "DATA":
            highest_sent = max(highest_sent, parse_message_id(d["message_id"])[2])
            most = max(most, highest_sent - acked)
        elif event == "ack_received":
            acked = d["acked_through"]
    return most


def test_go_back_n_without_loss(link):
    with FakeNetwork(link) as net:
        summary = go_back_n(link).run()

    assert summary["success"] is True
    assert (summary["acked"], summary["retransmissions"], summary["window"]) == (8, 0, 4)
    assert delivered(net.remote) == list(range(1, 9))


def test_go_back_n_fills_but_never_exceeds_its_window(link):
    with FakeNetwork(link, ack_delay=0.02) as net:
        summary = go_back_n(link, count=10, window=3).run()

    assert summary["success"] is True
    assert in_flight(link["events"]) == 3
    assert delivered(net.remote) == list(range(1, 11))


def test_window_of_one_keeps_one_message_in_flight(link):
    with FakeNetwork(link, ack_delay=0.01):
        summary = go_back_n(link, count=5, window=1).run()

    assert summary["success"] is True
    assert in_flight(link["events"]) == 1


def test_go_back_n_goes_back_after_a_lost_data_message(link):
    with FakeNetwork(link, drop_data=[("gbn-t-3", 1)], ack_delay=0.01) as net:
        summary = go_back_n(link).run()

    resent = [d["seq"] for e, d in link["events"] if e == "retransmit"]
    assert summary["success"] is True
    assert resent and resent[0] == 3                  # goes back to the lost message
    assert summary["retransmissions"] == len(resent)
    assert delivered(net.remote) == list(range(1, 9))  # still once and in order
    assert "dropped_out_of_order" in net.remote.names()


def test_a_later_cumulative_ack_covers_a_lost_ack(link):
    with FakeNetwork(link, drop_ack=[("gbn-t-2", 1)], ack_delay=0.01) as net:
        summary = go_back_n(link).run()

    assert summary["success"] is True
    assert summary["retransmissions"] == 0
    assert delivered(net.remote) == list(range(1, 9))
    assert any(d["newly_acked"] >= 2 for e, d in link["events"] if e == "ack_received")


def test_go_back_n_recovers_a_lost_final_ack(link):
    with FakeNetwork(link, drop_ack=[("gbn-t-8", 1)]) as net:
        summary = go_back_n(link).run()

    assert summary["success"] is True
    assert summary["retransmissions"] == 1
    assert net.receiver.summary()["duplicates"] == 1
    assert acks(net.remote).count("gbn-t-8") == 2


def test_go_back_n_gives_up_after_its_retry_limit(link):
    started = time.monotonic()
    summary = go_back_n(link, count=5, window=3, timeout=0.1, retries=2).run()
    took = time.monotonic() - started

    assert summary["success"] is False
    assert summary["failed_at"] == 1
    assert summary["retransmissions"] == 2 * 3          # each timeout resends the window
    assert 0.3 <= took < 0.8


def test_go_back_n_through_routers_with_forced_drops():
    config = make_route_config(load_config("config/topology.json"), "upper")
    sockets = {name: create_udp_socket("127.0.0.1", 0) for name in config["devices"]}
    for name, sock in sockets.items():
        config["devices"][name]["udp_port"] = sock.getsockname()[1]

    events = []

    def log(device, event, packet=None, **details):
        events.append((device, event, details))

    rules = {
        "routerB": [DropRule.parse("type=DATA,message_id=gbn-it-3")],
        "routerD": [DropRule.parse("type=ACK,message_id=gbn-it-6")],
    }
    routers = [Router(config, name, sock=sockets[name], log=log, drop_rules=rules.get(name))
               for name in ("routerA", "routerB", "routerC", "routerD")]
    host1 = Host(config, "host1", sock=sockets["host1"], log=log)
    host2 = Host(config, "host2", sock=sockets["host2"], log=log)

    stop = threading.Event()
    threads = [threading.Thread(target=r.serve, args=(stop, 0.02)) for r in routers]
    threads.append(threading.Thread(target=host2.serve, args=(stop, 0.02)))
    for thread in threads:
        thread.start()

    try:
        summary = GoBackNSender(host1, "host2", 6, "it", window=3, timeout=0.3,
                                retries=3, rate=100).run()
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=2)
        for sock in sockets.values():
            sock.close()

    assert summary["success"] is True
    assert summary["retransmissions"] >= 2
    assert [d["seq"] for dev, e, d in events if e == "delivered"] == [1, 2, 3, 4, 5, 6]
    assert sorted(dev for dev, e, d in events if e == "drop_forced") == ["routerB", "routerD"]


def test_cli_rejects_a_zero_window(capsys):
    assert main(["send", "--name", "host1", "--to", "host2", "--mode", "gobackn", "--window", "0"]) == 2
    assert "--window" in capsys.readouterr().err


def test_go_back_n_reads_acks_between_paced_sends(link):
    # ACKs come back at once and sends are 20 ms apart, so even a window of 8
    # should never have more than one message in flight.
    with FakeNetwork(link):
        summary = go_back_n(link, count=5, window=8, rate=50).run()

    assert summary["success"] is True
    assert in_flight(link["events"]) == 1
