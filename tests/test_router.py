
import threading
import time

import pytest

from scripts.make_routes import make_loop_config, make_route_config
from src.conditions import DropRule
from src.config import load_config
from src.host import Host
from src.router import Router, main
from src.transport import create_udp_socket, send_packet


@pytest.mark.parametrize("branch", ["upper", "lower"])
@pytest.mark.parametrize(
    "source,destination",
    [("host1", "host2"), ("host2", "host1")]
)
def test_multihop_communication(branch, source, destination):
    config = make_route_config(
        load_config("config/topology.json"),
        branch
    )

    sockets = {
        name: create_udp_socket("127.0.0.1", 0)
        for name in config["devices"]
    }

    stop = threading.Event()
    threads = []
    events = []

    def logger(device, event, packet=None, **details):
        events.append((device, event, details))

    try:
        for name, sock in sockets.items():
            config["devices"][name]["udp_port"] = sock.getsockname()[1]

        routers = [
            Router(config, name, sock=sockets[name], log=logger)
            for name in ("routerA", "routerB", "routerC", "routerD")
        ]

        hosts = {
            name: Host(config, name, sock=sockets[name], log=logger)
            for name in ("host1", "host2")
        }

        for router in routers:
            thread = threading.Thread(
                target=router.serve,
                args=(stop, 0.05)
            )
            threads.append(thread)

        threads.append(
            threading.Thread(
                target=hosts[destination].serve,
                args=(stop, 0.05)
            )
        )

        for thread in threads:
            thread.start()

        results = [
            hosts[source].send_probe(
                destination,
                payload="test",
                timeout=2
            )
            for _ in range(3)
        ]

        assert all(rtt is not None for rtt in results)

        middle = "routerB" if branch == "upper" else "routerC"

        forwarded_devices = {
            device
            for device, event, details in events
            if event == "forward"
        }

        assert {"routerA", middle, "routerD"} <= forwarded_devices

    finally:
        stop.set()

        for thread in threads:
            thread.join(timeout=2)

        for sock in sockets.values():
            sock.close()


def test_router_drops_expired_packet():
    config = load_config("config/topology.json")
    sock = create_udp_socket("127.0.0.1", 0)
    events = []

    def logger(device, event, packet=None, **details):
        events.append((event, details))

    router = Router(
        config,
        "routerA",
        sock=sock,
        log=logger
    )

    packet = {
        "src": "host1",
        "dst": "host2",
        "hop_limit": 1,
        "type": "PROBE",
        "message_id": "expired",
        "payload": "test"
    }

    try:
        result = router.handle(packet, ("127.0.0.1", 5001))

        assert result is False
        assert any(
            event == "drop"
            and details.get("reason") == "hop_limit_exhausted"
            for event, details in events
        )
    finally:
        router.close()


def test_router_logs_settings_at_startup():
    config = load_config("config/topology.json")
    config["links"][1]["loss"] = 0.05  # routerA-routerB
    sock = create_udp_socket("127.0.0.1", 0)
    port = sock.getsockname()[1]
    events = []

    def logger(device, event, packet=None, **details):
        events.append((event, details))

    router = Router(
        config, "routerA", sock=sock, log=logger, seed=42,
        drop_rules=[DropRule.parse("type=PROBE")]
    )

    try:
        router.log_start()
    finally:
        router.close()

    event, details = events[0]
    assert event == "started"
    assert details["seed"] == 42
    assert details["endpoint"] == f"127.0.0.1:{port}"
    assert {"neighbor": "routerB", "loss": 0.05, "delay_ms": 0} in details["links"]
    assert len(details["links"]) == 3
    assert details["drop_rules"] == ["type=PROBE,skip=0,count=1"]


def test_forwarding_loop_ends_at_hop_limit():
    config = make_loop_config(load_config("config/topology.json"))
    sockets = {
        name: create_udp_socket("127.0.0.1", 0)
        for name in ("host1", "routerA", "routerB")
    }
    for name, sock in sockets.items():
        config["devices"][name]["udp_port"] = sock.getsockname()[1]

    events = []

    def logger(device, event, packet=None, **details):
        events.append((device, event, details))

    routers = [
        Router(config, name, sock=sockets[name], log=logger)
        for name in ("routerA", "routerB")
    ]
    stop = threading.Event()
    threads = [
        threading.Thread(target=router.serve, args=(stop, 0.05))
        for router in routers
    ]
    for thread in threads:
        thread.start()

    try:
        send_packet(sockets["host1"], sockets["routerA"].getsockname(), {
            "src": "host1",
            "dst": "routerD",
            "hop_limit": 6,
            "type": "PROBE",
            "message_id": "loop-1",
            "payload": "loop",
        })

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if any(event == "drop" for _, event, _ in events):
                break
            time.sleep(0.02)
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=2)
        for sock in sockets.values():
            sock.close()

    forwards = [device for device, event, _ in events if event == "forward"]
    drops = [
        (device, details.get("reason"))
        for device, event, details in events
        if event == "drop"
    ]
    assert forwards == ["routerA", "routerB", "routerA", "routerB", "routerA"]
    assert drops == [("routerB", "hop_limit_exhausted")]


def test_router_cli_rejects_bad_drop_rule(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--name", "routerA", "--drop-rule", "colour=red"])

    assert exc.value.code == 2
    assert "unknown drop rule field" in capsys.readouterr().err
