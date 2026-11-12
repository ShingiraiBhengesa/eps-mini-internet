
import argparse
import socket
import sys

from .conditions import DropRule, LinkScheduler
from .config import ConfigError, get_endpoint, load_config
from .forwarding_checks import ForwardingError, plan_forward
from .packet import PacketError
from .run_log import DEFAULT_LOG_DIR, create_logger, log_path, run_id_arg
from .shutdown import stop_on_signals
from .transport import create_udp_socket, receive_packet


class Router:
    def __init__(self, config, name, sock=None, log=None, seed=0, drop_rules=None):
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
            log=self.log,
            drop_rules=drop_rules
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

    def log_start(self):
        """Record everything needed to reproduce this router's behavior."""
        host, port = self.sock.getsockname()[:2]
        self.log(
            self.name,
            "started",
            endpoint=f"{host}:{port}",
            seed=self.scheduler.seed,
            links=self.scheduler.link_settings(),
            drop_rules=[rule.describe() for rule in self.scheduler.drop_rules],
        )

    def serve(self, stop=None, poll_interval=0.1):
        self.log_start()

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


def drop_rule_arg(text):
    try:
        return DropRule.parse(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a mini-internet router.")
    parser.add_argument("--name", required=True)
    parser.add_argument("--config", default="config/topology.json")
    parser.add_argument("--run-id", type=run_id_arg, default="default")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--log-dir",
        default=DEFAULT_LOG_DIR,
        help="write this router's log to LOG_DIR/RUN_ID/NAME.jsonl "
             "(also printed to the terminal)",
    )
    parser.add_argument(
        "--drop-rule",
        type=drop_rule_arg,
        action="append",
        default=[],
        metavar="RULE",
        help="drop matching packets this router sends, e.g. "
             "'type=PROBE,skip=1' drops the second PROBE once; repeatable",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        router = Router(
            config,
            args.name,
            log=create_logger(
                args.run_id,
                path=log_path(args.log_dir, args.run_id, args.name),
            ),
            seed=args.seed,
            drop_rules=args.drop_rule,
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
        router.log.close()


if __name__ == "__main__":
    stop_on_signals()
    sys.exit(main())
