
import threading

import pytest

from src.config import ConfigError, load_config
from src.host import Host, main
from src.packet import decode_packet, encode_packet
from src.transport import create_udp_socket, receive_packet, send_packet


class Recorder:
    """Collect log events for assertions."""

    def __init__(self):
        self.events = []

    def __call__(self, device, event, packet=None, **details):
        self.events.append(event)


@pytest.fixture
def net():
    """Two hosts and their gateways on ephemeral localhost ports."""
    config = load_config("config/topology.json")
    sockets = {}

    for name in ("host1", "host2", "routerA", "routerD"):
        sock = create_udp_socket("127.0.0.1", 0)
        sock.settimeout(2)
        sockets[name] = sock
        config["devices"][name]["udp_port"] = sock.getsockname()[1]

    log1, log2 = Recorder(), Recorder()
    host1 = Host(config, "host1", sock=sockets["host1"], log=log1)
    host2 = Host(config, "host2", sock=sockets["host2"], log=log2)

    yield {
        "config": config,
        "host1": host1,
        "host2": host2,
        "routerA": sockets["routerA"],
        "routerD": sockets["routerD"],
        "log1": log1,
        "log2": log2,
    }

    for sock in sockets.values():
        sock.close()


def probe_packet(message_id="m1", dst="host2"):
    return {
        "src": "host1",
        "dst": dst,
        "hop_limit": 10,
        "type": "PROBE",
        "message_id": message_id,
        "payload": "hi",
    }


def test_rejects_non_host_name(net):
    with pytest.raises(ConfigError):
        Host(net["config"], "routerA", sock=net["routerA"])


def test_host_answers_probe_with_echo(net):
    host2_address = net["host2"].sock.getsockname()
    send_packet(net["routerD"], host2_address, probe_packet())

    packet = net["host2"].receive(2)
    assert net["host2"].handle(packet) is None

    echo, sender = receive_packet(net["routerD"])
    assert sender == host2_address
    assert echo == {
        "src": "host2",
        "dst": "host1",
        "hop_limit": 16,
        "type": "ECHO",
        "message_id": "m1",
        "payload": "hi",
    }


def test_probe_goes_to_gateway_and_gets_reply(net):
    def fake_router():
        probe, sender = receive_packet(net["routerA"])
        echo = dict(probe, src=probe["dst"], dst=probe["src"], type="ECHO")
        send_packet(net["routerA"], sender, echo)

    thread = threading.Thread(target=fake_router)
    thread.start()

    rtt = net["host1"].send_probe("host2", timeout=2)
    thread.join()

    assert rtt is not None and rtt >= 0
    assert "echo_received" in net["log1"].events


def test_probe_times_out(net):
    assert net["host1"].send_probe("host2", timeout=0.1) is None
    assert "probe_timeout" in net["log1"].events


def test_probe_ignores_echo_with_wrong_id(net):
    def fake_router():
        probe, sender = receive_packet(net["routerA"])
        stale = dict(
            probe,
            src="host2",
            dst="host1",
            type="ECHO",
            message_id="old",
        )
        send_packet(net["routerA"], sender, stale)

    thread = threading.Thread(target=fake_router)
    thread.start()

    assert net["host1"].send_probe("host2", timeout=0.3) is None
    thread.join()
    assert "dropped_unexpected_echo" in net["log1"].events


def test_drops_packet_for_other_destination(net):
    send_packet(
        net["routerD"],
        net["host2"].sock.getsockname(),
        probe_packet(dst="host1"),
    )

    packet = net["host2"].receive(2)
    assert net["host2"].handle(packet) is None
    assert "dropped_wrong_destination" in net["log2"].events


def test_drops_malformed_datagram(net):
    net["routerD"].sendto(b"{not json", net["host2"].sock.getsockname())

    assert net["host2"].receive(2) is None
    assert "dropped_malformed" in net["log2"].events


def test_end_to_end_probe_through_relay(net):
    """host1 -> relay -> host2 -> relay -> host1, with host2 serving."""
    config = net["config"]
    stop = threading.Event()

    def relay(sock):
        sock.settimeout(0.1)
        while not stop.is_set():
            try:
                data, _ = sock.recvfrom(1201)
            except OSError:
                continue
            packet = decode_packet(data)
            dst = config["devices"][packet["dst"]]
            sock.sendto(
                encode_packet(packet),
                (dst["udp_host"], dst["udp_port"]),
            )

    threads = [
        threading.Thread(target=relay, args=(net["routerA"],)),
        threading.Thread(target=relay, args=(net["routerD"],)),
        threading.Thread(target=net["host2"].serve, args=(stop, 0.1)),
    ]
    for thread in threads:
        thread.start()

    try:
        rtt = net["host1"].send_probe("host2", payload="ping", timeout=2)
    finally:
        stop.set()
        for thread in threads:
            thread.join()

    assert rtt is not None
    assert "received" in net["log2"].events


def test_cli_rejects_unknown_host(capsys):
    assert main(["--name", "hostZ", "--probe", "host2"]) == 2
    assert "error" in capsys.readouterr().err


def test_cli_rejects_unknown_destination(capsys):
    assert main(["--name", "host1", "--probe", "hostZ"]) == 2
    assert "unknown destination" in capsys.readouterr().err
