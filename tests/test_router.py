
import threading

import pytest

from scripts.make_routes import make_route_config
from src.config import load_config
from src.host import Host
from src.router import Router
from src.transport import create_udp_socket


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
