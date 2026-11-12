
import argparse
import socket
import sys
import time
import uuid

from .config import ConfigError, get_endpoint, load_config
from .packet import PacketError
from .run_log import DEFAULT_LOG_DIR, create_logger, log_path, run_id_arg
from .shutdown import stop_on_signals
from .transport import create_udp_socket, receive_packet, send_packet

DEFAULT_HOP_LIMIT = 16


class Host:
    """A host that sends probes and answers them with echoes."""

    def __init__(
        self,
        config,
        name,
        sock=None,
        log=None,
        hop_limit=DEFAULT_HOP_LIMIT,
    ):
        device = config["devices"].get(name)
        if device is None or device["role"] != "host":
            raise ConfigError(f"{name} is not a host in the configuration")

        self.config = config
        self.name = name
        self.gateway = device["gateway"]
        self.gateway_endpoint = get_endpoint(config, self.gateway)
        self.hop_limit = hop_limit
        self.log = log if log is not None else create_logger("default")
        self.sock = sock or create_udp_socket(*get_endpoint(config, name))
        # Probe IDs are <host>-<session>-<n>: the session part keeps a late
        # echo from an earlier program run from matching a new probe.
        self.session = uuid.uuid4().hex[:8]
        self.probes_sent = 0

    def close(self):
        self.sock.close()

    def log_start(self, mode):
        host, port = self.sock.getsockname()[:2]
        self.log(
            self.name,
            "started",
            mode=mode,
            endpoint=f"{host}:{port}",
            gateway=self.gateway,
            hop_limit=self.hop_limit,
        )

    def send_probe(self, dst, payload="", timeout=2.0):
        """Send one PROBE and wait for an ECHO with the same ID and payload.

        Returns the round-trip time in milliseconds, or None on timeout.
        """
        self.probes_sent += 1
        message_id = f"{self.name}-{self.session}-{self.probes_sent}"
        probe = self._make_packet("PROBE", dst, message_id, payload)

        start = time.monotonic()
        deadline = start + timeout
        self._send(probe)

        while (remaining := deadline - time.monotonic()) > 0:
            packet = self.receive(remaining)
            if packet is None:
                continue

            echo = self.handle(packet)
            if echo is None:
                continue

            if echo["message_id"] != message_id or echo["src"] != dst:
                self.log(self.name, "dropped_unexpected_echo", echo)
                continue

            if echo["payload"] != payload:
                self.log(
                    self.name, "echo_payload_mismatch", echo,
                    expected_payload=payload, received_payload=echo["payload"],
                )
                continue

            rtt_ms = (time.monotonic() - start) * 1000
            self.log(
                self.name, "echo_received", echo,
                rtt_ms=round(rtt_ms, 3), payload_bytes=len(payload),
            )
            return rtt_ms

        self.log(self.name, "probe_timeout", probe, timeout_s=timeout)
        return None

    def serve(self, stop=None, poll_interval=0.2):
        """Answer probes until stop is set (or forever without one)."""
        while stop is None or not stop.is_set():
            packet = self.receive(poll_interval)
            if packet is None:
                continue

            echo = self.handle(packet)
            if echo is not None:
                self.log(self.name, "dropped_unexpected_echo", echo)

    def receive(self, timeout):
        """Receive one valid packet, or None on timeout or malformed data."""
        self.sock.settimeout(timeout)

        try:
            packet, sender = receive_packet(self.sock)
        except socket.timeout:
            return None
        except PacketError as exc:
            self.log(self.name, "dropped_malformed", reason=exc)
            return None

        return packet

    def handle(self, packet):
        """Process a packet; return it if it is an ECHO for this host."""
        if packet["dst"] != self.name:
            self.log(self.name, "dropped_wrong_destination", packet)
            return None

        self.log(self.name, "received", packet)

        if packet["type"] == "PROBE":
            echo = self._make_packet(
                "ECHO",
                packet["src"],
                packet["message_id"],
                packet["payload"],
            )
            self._send(echo)
            return None

        if packet["type"] == "ECHO":
            return packet

        self.log(self.name, "dropped_unhandled_type", packet)
        return None

    def _make_packet(self, packet_type, dst, message_id, payload):
        return {
            "src": self.name,
            "dst": dst,
            "hop_limit": self.hop_limit,
            "type": packet_type,
            "message_id": message_id,
            "payload": payload,
        }

    def _send(self, packet):
        send_packet(self.sock, self.gateway_endpoint, packet)
        self.log(self.name, "sent", packet, next_hop=self.gateway)


def default_payload(number):
    """Same length for every probe, so message size stays fixed."""
    return f"probe-{number:04d}"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a mini-internet host.")
    parser.add_argument("--config", default="config/topology.json")
    parser.add_argument("--name", required=True, help="this host's name")
    parser.add_argument(
        "--probe",
        metavar="DST",
        help="send probes to DST and exit; without it, answer probes until stopped",
    )
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--hop-limit", type=int, default=DEFAULT_HOP_LIMIT)
    parser.add_argument(
        "--payload",
        help="payload for every probe (default: probe-0001, probe-0002, ...)",
    )
    parser.add_argument("--run-id", type=run_id_arg, default="default")
    parser.add_argument(
        "--log-dir",
        default=DEFAULT_LOG_DIR,
        help="write this host's log to LOG_DIR/RUN_ID/NAME.jsonl "
             "(also printed to the terminal)",
    )
    args = parser.parse_args(argv)

    log = create_logger(
        args.run_id,
        path=log_path(args.log_dir, args.run_id, args.name),
    )

    try:
        config = load_config(args.config)
        if args.probe is not None and args.probe not in config["devices"]:
            raise ConfigError(f"unknown destination {args.probe}")
        host = Host(config, args.name, log=log, hop_limit=args.hop_limit)
    except (ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        if args.probe is None:
            host.log_start("serve")
            host.serve()
            return 0

        host.log_start("probe")
        answered = 0
        for i in range(args.count):
            if i:
                time.sleep(args.interval)
            payload = args.payload if args.payload is not None else default_payload(i + 1)
            if host.send_probe(args.probe, payload, args.timeout) is not None:
                answered += 1

        print(f"{answered}/{args.count} probes answered", file=sys.stderr)
        return 0 if answered == args.count else 1
    except PacketError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    finally:
        host.close()
        log.close()


if __name__ == "__main__":
    stop_on_signals()
    sys.exit(main())
