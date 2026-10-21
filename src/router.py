
import argparse
import socket
import sys

from .conditions import LinkScheduler
from .config import ConfigError, get_endpoint, load_config
from .forwarding_checks import ForwardingError, plan_forward
from .packet import PacketError
from .run_log import create_logger
from .transport import create_udp_socket, receive_packet


class Router:
    def __init__(self, config, name, sock=None, log=None, seed=0):
        device = config["devices"].get(name)

        if device is None or device["role"] != "router":
            raise ConfigError(f"{name} is not a router")

        self.config = config
        self.name = name
        self.sock = sock if sock is not None else create_udp_socket(
            *get_endpoint(config, name)
        )
        self.log = log if log is not None else create_logger("default")

        self.scheduler = LinkScheduler(
            config,
            name,
            self.sock,
            seed=seed,
            log=self.log
        )

    def handle(self, packet, sender):
        self.log(
            self.name,
            "received",
            packet,
            sender_endpoint=f"{sender[0]}:{sender[1]}"
        )

        try:
            next_hop, endpoint, forwarded = plan_forward(
                self.config,
                self.name,
                packet,
                sender
            )
        except ForwardingError as exc:
            self.log(self.name, "drop", packet, reason=str(exc))
            return False

        self.log(
            self.name,
            "forward",
            forwarded,
            next_hop=next_hop
        )

        return self.scheduler.enqueue(next_hop, forwarded)

    def serve(self, stop=None, poll_interval=0.1):
        self.log(self.name, "started")

        while stop is None or not stop.is_set():
            self.scheduler.send_ready()

            timeout = max(
                0.001,
                self.scheduler.next_timeout(poll_interval)
            )
            self.sock.settimeout(timeout)

            try:
                packet, sender = receive_packet(self.sock)
            except socket.timeout:
                continue
            except PacketError as exc:
                self.log(
                    self.name,
                    "drop",
                    reason="malformed_packet",
                    detail=str(exc)
                )
                continue

            self.handle(packet, sender)
            self.scheduler.send_ready()

    def close(self):
        self.sock.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--config", default="config/topology.json")
    parser.add_argument("--run-id", default="baseline")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        logger = create_logger(args.run_id)
        router = Router(
            config,
            args.name,
            log=logger,
            seed=args.seed
        )
    except (ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        router.serve()
    except KeyboardInterrupt:
        return 0
    finally:
        router.close()


if __name__ == "__main__":
    sys.exit(main())
