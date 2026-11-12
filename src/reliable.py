"""Track B: reliable transfer with stop-and-wait, plus a best-effort baseline
and, as the optional enhancement, a Go-Back-N sliding window.

Message IDs carry the protocol's state: "<mode>-<transfer>-<seq>".
    mode      "sw" (stop-and-wait), "be" (best-effort) or "gbn" (Go-Back-N)
    transfer  names one transfer: letters, digits, '.', '_' and '-'
    seq       message number, 1 to N
An ACK reuses the message ID of the DATA it acknowledges, the same way an
ECHO reuses its probe's ID, so the packet format is unchanged. A Go-Back-N
ACK is cumulative: the ACK for message k means messages 1 to k all arrived.

    python -m src.reliable send --name host1 --to host2 --count 20
    python -m src.reliable receive --name host2 --expect 20
"""

import argparse
import re
import sys
import time
import uuid
from dataclasses import dataclass, field

from .config import ConfigError, load_config
from .packet import PacketError
from .run_log import DEFAULT_LOG_DIR, create_logger, log_path, run_id_arg
from .shutdown import stop_on_signals

MODES = {"stopwait": "sw", "besteffort": "be", "gobackn": "gbn"}
MODE_NAMES = {code: name for name, code in MODES.items()}
TRANSFER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$")

DEFAULT_COUNT = 20
DEFAULT_RATE = 20.0       # messages per second, the most either sender sends
DEFAULT_TIMEOUT = 0.5     # seconds to wait for each ACK
DEFAULT_RETRIES = 5       # resends allowed per message before giving up
DEFAULT_LINGER = 3.0      # seconds the receiver stays after the last message
DEFAULT_WINDOW = 4        # Go-Back-N: most messages in flight at once


def message_id(mode, transfer, seq):
    return f"{MODES[mode]}-{transfer}-{seq}"


def parse_message_id(text):
    """Return (mode, transfer, seq) for a Track B message ID, else None."""
    code, sep, rest = text.partition("-")
    if not sep or code not in MODE_NAMES:
        return None
    transfer, sep, seq = rest.rpartition("-")
    if not sep or not TRANSFER_PATTERN.match(transfer) or not seq.isdigit():
        return None
    number = int(seq)
    return (MODE_NAMES[code], transfer, number) if number >= 1 else None


def data_payload(seq):
    """Same length for every message, so message size stays fixed."""
    return f"data-{seq:04d}"


def new_transfer_id():
    return uuid.uuid4().hex[:6]


# ---------------------------------------------------------------- receiver

@dataclass
class TransferState:
    src: str
    mode: str
    transfer: str
    first_at: float
    expected: int = 1             # stop-and-wait: next sequence number to deliver
    delivered: list = field(default_factory=list)
    duplicates: int = 0
    completed_at: float = None


class Receiver:
    """Handles DATA arriving at one host.

    Stop-and-wait: only the next expected message is delivered, exactly once
    and in order, and ACKed. A copy of an earlier message is not delivered
    again but is ACKed again, because its first ACK may have been lost. A
    message from further ahead is ignored without an ACK.

    Best-effort: every new message is delivered once, in any order, and
    nothing is ACKed.

    Go-Back-N: only the next expected message is delivered, as in
    stop-and-wait, but every ACK is cumulative: after any arrival the
    receiver ACKs the highest message it has delivered in order. A copy of
    an earlier message or a message from further ahead is not delivered; it
    re-ACKs that highest message, which tells the sender where to go back to.

    With `expect` set, the receiver reports itself finished `linger` seconds
    after the last expected message arrives, so a resent final message still
    gets its ACK. With `observe` set, it finishes that many seconds after the
    transfer's first message, whatever has arrived by then.
    """

    def __init__(self, host, expect=None, linger=DEFAULT_LINGER, observe=None,
                 clock=time.monotonic):
        self.host = host
        self.expect = expect
        self.linger = linger
        self.observe = observe
        self.clock = clock
        self.transfers = {}
        self.current = None

    def log(self, event, packet=None, **details):
        self.host.log(self.host.name, event, packet, **details)

    def on_data(self, packet):
        parsed = parse_message_id(packet["message_id"])
        if parsed is None:
            self.log("dropped_bad_data_id", packet)
            return

        mode, transfer, seq = parsed
        key = (packet["src"], mode, transfer)
        state = self.transfers.get(key)
        if state is None:
            state = TransferState(packet["src"], mode, transfer, self.clock())
            self.transfers[key] = state
        self.current = state

        if mode == "stopwait":
            self._stop_and_wait(state, packet, seq)
        elif mode == "gobackn":
            self._go_back_n(state, packet, seq)
        else:
            self._best_effort(state, packet, seq)

        if (self.expect is not None and state.completed_at is None
                and len(set(state.delivered)) >= self.expect):
            state.completed_at = self.clock()
            self.log("transfer_received", transfer=transfer, mode=mode,
                     expected=self.expect, linger_s=self.linger)

    def _stop_and_wait(self, state, packet, seq):
        if seq == state.expected:
            state.delivered.append(seq)
            state.expected += 1
            self.log("delivered", packet, seq=seq, mode="stopwait")
            self._ack(packet)
        elif seq < state.expected:
            state.duplicates += 1
            self.log("duplicate", packet, seq=seq, acked=True)
            self._ack(packet)
        else:
            self.log("dropped_out_of_order", packet, seq=seq, expected=state.expected)

    def _go_back_n(self, state, packet, seq):
        if seq == state.expected:
            state.delivered.append(seq)
            state.expected += 1
            self.log("delivered", packet, seq=seq, mode="gobackn")
            self._ack_through(packet, state, seq)
            return

        highest = state.expected - 1
        if seq < state.expected:
            state.duplicates += 1
            self.log("duplicate", packet, seq=seq, acked=True, acked_through=highest)
        else:
            self.log("dropped_out_of_order", packet, seq=seq, expected=state.expected,
                     acked_through=highest if highest else None)
        if highest:
            self._ack_through(packet, state, highest)

    def _ack_through(self, data, state, highest):
        """Cumulative ACK: messages 1 to `highest` have all been delivered."""
        mid = message_id(state.mode, state.transfer, highest)
        self.host._send(self.host._make_packet("ACK", data["src"], mid, ""))

    def _best_effort(self, state, packet, seq):
        if seq in state.delivered:
            state.duplicates += 1
            self.log("duplicate", packet, seq=seq, acked=False)
            return
        state.delivered.append(seq)
        self.log("delivered", packet, seq=seq, mode="besteffort")

    def _ack(self, data):
        ack = self.host._make_packet("ACK", data["src"], data["message_id"], "")
        self.host._send(ack)

    def finished(self):
        """Why the receiver can stop now, or None to keep going."""
        state = self.current
        if state is None:
            return None
        now = self.clock()
        if state.completed_at is not None and now - state.completed_at >= self.linger:
            return "linger_over"
        if self.observe is not None and now - state.first_at >= self.observe:
            return "observation_over"
        return None

    def summary(self):
        state = self.current
        if state is None:
            return {"transfer": None, "delivered": 0}

        unique = sorted(set(state.delivered))
        result = {
            "transfer": state.transfer,
            "mode": state.mode,
            "src": state.src,
            "delivered": len(unique),
            "duplicates": state.duplicates,
            # No message delivered after a later one (gaps from loss don't count).
            "in_order": state.delivered == sorted(state.delivered),
            "elapsed_s": round(self.clock() - state.first_at, 3),
        }
        if self.expect is not None:
            missing = [n for n in range(1, self.expect + 1) if n not in set(unique)]
            result.update(expected=self.expect, complete=not missing,
                          missing=missing[:50])
        return result


# ---------------------------------------------------------------- senders

class Pacer:
    """Keeps sends at least 1/rate seconds apart."""

    def __init__(self, rate, clock=time.monotonic, sleep=time.sleep):
        self.gap = 1.0 / rate
        self.clock = clock
        self.sleep = sleep
        self.next_at = None

    def wait(self):
        now = self.clock()
        if self.next_at is not None and now < self.next_at:
            self.sleep(self.next_at - now)
            now = self.next_at
        self.next_at = now + self.gap


class StopAndWaitSender:
    """Send message k, wait for its ACK, then send message k + 1.

    If no ACK arrives within `timeout`, resend k and count a retransmission.
    After `retries` resends of the same message, give up with a failure.
    ACKs for earlier messages (late or duplicate) and for other transfers
    are ignored.
    """

    mode = "stopwait"

    def __init__(self, host, dst, count, transfer, timeout=DEFAULT_TIMEOUT,
                 retries=DEFAULT_RETRIES, rate=DEFAULT_RATE,
                 clock=time.monotonic, sleep=time.sleep):
        self.host = host
        self.dst = dst
        self.count = count
        self.transfer = transfer
        self.timeout = timeout
        self.retries = retries
        self.rate = rate
        self.clock = clock
        self.pacer = Pacer(rate, clock, sleep)

    def log(self, event, packet=None, **details):
        self.host.log(self.host.name, event, packet, **details)

    def run(self):
        self.log("transfer_started", mode=self.mode, transfer=self.transfer,
                 dst=self.dst, messages=self.count, rate=self.rate,
                 timeout_s=self.timeout, retries=self.retries)
        start = self.clock()
        acked = retransmissions = 0

        for seq in range(1, self.count + 1):
            mid = message_id(self.mode, self.transfer, seq)
            packet = self.host._make_packet("DATA", self.dst, mid, data_payload(seq))
            attempt = 0

            while True:
                attempt += 1
                self.pacer.wait()
                if attempt > 1:
                    retransmissions += 1
                    self.log("retransmit", packet, seq=seq, attempt=attempt)
                sent_at = self.clock()
                self.host._send(packet)

                if self._wait_for_ack(mid, seq, sent_at, attempt):
                    acked += 1
                    break

                self.log("ack_timeout", packet, seq=seq, attempt=attempt,
                         timeout_s=self.timeout)
                if attempt > self.retries:
                    self.log("transfer_failed", packet, seq=seq, attempts=attempt)
                    return self._finish(start, acked, retransmissions, failed_at=seq)

        return self._finish(start, acked, retransmissions)

    def _wait_for_ack(self, mid, seq, sent_at, attempt):
        deadline = sent_at + self.timeout
        while (remaining := deadline - self.clock()) > 0:
            packet = self.host.receive(remaining)
            if packet is None:
                continue
            reply = self.host.handle(packet)
            if reply is None:
                continue
            if reply["type"] != "ACK":
                self.log("dropped_unexpected_echo", reply)
                continue
            if reply["message_id"] == mid and reply["src"] == self.dst:
                rtt_ms = (self.clock() - sent_at) * 1000
                self.log("ack_received", reply, seq=seq, attempt=attempt,
                         rtt_ms=round(rtt_ms, 3))
                return True
            self.log("ack_ignored", reply, expected_id=mid,
                     reason=self._why_ignored(reply))
        return False

    def _why_ignored(self, ack):
        parsed = parse_message_id(ack["message_id"])
        if parsed and parsed[:2] == (self.mode, self.transfer):
            return "late or duplicate ACK for an earlier message"
        return "ACK for another transfer"

    def _finish(self, start, acked, retransmissions, failed_at=None):
        summary = {
            "mode": self.mode,
            "transfer": self.transfer,
            "messages": self.count,
            "acked": acked,
            "retransmissions": retransmissions,
            "elapsed_s": round(self.clock() - start, 3),
            "success": failed_at is None,
            "failed_at": failed_at,
        }
        self.log("transfer_done", **summary)
        return summary


class GoBackNSender:
    """Go-Back-N sliding window: up to `window` messages in flight at once.

    This is the optional enhancement; stop-and-wait above is unchanged. The
    sender fills the window, then waits. One timer runs for the oldest
    unacknowledged message (the base). A cumulative ACK for message k slides
    the window to k + 1 and restarts the timer if messages are still in
    flight. When the timer expires, the sender goes back: it resends every
    message from the base onwards, counting each as a retransmission. After
    `retries` timeouts in a row without the base moving, it gives up. Late or
    duplicate ACKs (for messages already acknowledged) are ignored. With a
    window of 1 it behaves like stop-and-wait.
    """

    mode = "gobackn"

    def __init__(self, host, dst, count, transfer, window=DEFAULT_WINDOW,
                 timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES, rate=DEFAULT_RATE,
                 clock=time.monotonic, sleep=time.sleep):
        self.host = host
        self.dst = dst
        self.count = count
        self.transfer = transfer
        self.window = window
        self.timeout = timeout
        self.retries = retries
        self.rate = rate
        self.clock = clock
        self.pacer = Pacer(rate, clock, sleep)
        self.base = 1          # oldest message not yet acknowledged
        self.next_seq = 1      # next new message to send
        self.attempts = {}     # message -> times sent
        self.sent_at = {}      # message -> when it was last sent

    def log(self, event, packet=None, **details):
        self.host.log(self.host.name, event, packet, **details)

    def _packet(self, seq):
        mid = message_id(self.mode, self.transfer, seq)
        return self.host._make_packet("DATA", self.dst, mid, data_payload(seq))

    def _send(self, seq):
        packet = self._packet(seq)
        self.pacer.wait()
        self.attempts[seq] = self.attempts.get(seq, 0) + 1
        if self.attempts[seq] > 1:
            self.log("retransmit", packet, seq=seq, attempt=self.attempts[seq])
        self.sent_at[seq] = self.clock()
        self.host._send(packet)

    def run(self):
        self.log("transfer_started", mode=self.mode, transfer=self.transfer,
                 dst=self.dst, messages=self.count, window=self.window,
                 rate=self.rate, timeout_s=self.timeout, retries=self.retries)
        start = self.clock()
        timer = None           # when the base was last sent
        stalls = 0             # timeouts in a row without the base moving
        retransmissions = 0

        while self.base <= self.count:
            # Fill the window with new messages.
            while self.next_seq <= self.count and self.next_seq < self.base + self.window:
                self._send(self.next_seq)
                if self.next_seq == self.base:
                    timer = self.sent_at[self.base]
                self.next_seq += 1

            remaining = timer + self.timeout - self.clock()
            if remaining > 0:
                acked = self._receive_ack(remaining)
                if acked is not None:
                    self.base = acked + 1
                    stalls = 0
                    timer = self.clock() if self.base < self.next_seq else None
                continue

            # Timeout: go back and resend everything from the base.
            stalls += 1
            self.log("ack_timeout", self._packet(self.base), seq=self.base,
                     attempt=self.attempts[self.base], timeout_s=self.timeout,
                     in_flight=self.next_seq - self.base)
            if stalls > self.retries:
                self.log("transfer_failed", self._packet(self.base), seq=self.base,
                         attempts=self.attempts[self.base])
                return self._finish(start, self.base - 1, retransmissions, failed_at=self.base)
            for seq in range(self.base, self.next_seq):
                self._send(seq)
                retransmissions += 1
                if seq == self.base:
                    timer = self.sent_at[seq]

        return self._finish(start, self.count, retransmissions)

    def _receive_ack(self, wait):
        """Wait up to `wait` seconds; return k for a new cumulative ACK, else None."""
        packet = self.host.receive(wait)
        if packet is None:
            return None
        reply = self.host.handle(packet)
        if reply is None:
            return None
        if reply["type"] != "ACK":
            self.log("dropped_unexpected_echo", reply)
            return None

        parsed = parse_message_id(reply["message_id"])
        if reply["src"] != self.dst or not parsed or parsed[:2] != (self.mode, self.transfer):
            self.log("ack_ignored", reply, reason="ACK for another transfer")
            return None
        k = parsed[2]
        if k < self.base:
            self.log("ack_ignored", reply, reason="duplicate ACK: everything up to "
                     f"#{k} was already acknowledged")
            return None
        if k >= self.next_seq:
            self.log("ack_ignored", reply, reason=f"ACK for #{k}, which was never sent")
            return None

        rtt_ms = (self.clock() - self.sent_at[k]) * 1000
        self.log("ack_received", reply, seq=k, acked_through=k,
                 newly_acked=k - self.base + 1, attempt=self.attempts[k],
                 rtt_ms=round(rtt_ms, 3))
        return k

    def _finish(self, start, acked, retransmissions, failed_at=None):
        summary = {
            "mode": self.mode,
            "transfer": self.transfer,
            "window": self.window,
            "messages": self.count,
            "acked": acked,
            "retransmissions": retransmissions,
            "elapsed_s": round(self.clock() - start, 3),
            "success": failed_at is None,
            "failed_at": failed_at,
        }
        self.log("transfer_done", **summary)
        return summary


class BestEffortSender:
    """The comparison baseline: same messages and rate, no ACKs, no resends."""

    mode = "besteffort"

    def __init__(self, host, dst, count, transfer, rate=DEFAULT_RATE,
                 clock=time.monotonic, sleep=time.sleep):
        self.host = host
        self.dst = dst
        self.count = count
        self.transfer = transfer
        self.rate = rate
        self.clock = clock
        self.pacer = Pacer(rate, clock, sleep)

    def run(self):
        self.host.log(self.host.name, "transfer_started", mode=self.mode,
                      transfer=self.transfer, dst=self.dst,
                      messages=self.count, rate=self.rate)
        start = self.clock()

        for seq in range(1, self.count + 1):
            self.pacer.wait()
            mid = message_id(self.mode, self.transfer, seq)
            self.host._send(self.host._make_packet("DATA", self.dst, mid, data_payload(seq)))

        summary = {
            "mode": self.mode,
            "transfer": self.transfer,
            "messages": self.count,
            "acked": None,
            "retransmissions": 0,
            "elapsed_s": round(self.clock() - start, 3),
            "success": None,
            "failed_at": None,
        }
        self.host.log(self.host.name, "transfer_done", **summary)
        return summary


# ---------------------------------------------------------------- command line

def transfer_id_arg(value):
    if not TRANSFER_PATTERN.match(value):
        raise argparse.ArgumentTypeError(
            "transfer ID must be 1-48 characters: letters, digits, '.', '_' or '-', "
            "starting with a letter or digit"
        )
    return value


def positive_float(value):
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return number


def build_parser():
    parser = argparse.ArgumentParser(
        description="Track B: stop-and-wait reliable transfer and a best-effort baseline."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def common(sub):
        sub.add_argument("--config", default="config/topology.json")
        sub.add_argument("--name", required=True, help="this host's name")
        sub.add_argument("--run-id", type=run_id_arg, default="default")
        sub.add_argument("--log-dir", default=DEFAULT_LOG_DIR)
        sub.add_argument("--hop-limit", type=int, default=16)

    send = commands.add_parser("send", help="send COUNT messages to another host")
    common(send)
    send.add_argument("--to", required=True, metavar="DST", help="destination host")
    send.add_argument("--mode", choices=sorted(MODES), default="stopwait")
    send.add_argument("--count", type=int, default=DEFAULT_COUNT, help="number of messages")
    send.add_argument("--rate", type=positive_float, default=DEFAULT_RATE,
                      help="most messages sent per second (default %(default)s)")
    send.add_argument("--timeout", type=positive_float, default=DEFAULT_TIMEOUT,
                      help="seconds to wait for each ACK (stop-and-wait)")
    send.add_argument("--retries", type=int, default=DEFAULT_RETRIES,
                      help="resends allowed per message before giving up (stop-and-wait)")
    send.add_argument("--window", type=int, default=DEFAULT_WINDOW,
                      help="most messages in flight at once (gobackn, default %(default)s)")
    send.add_argument("--transfer-id", type=transfer_id_arg,
                      help="name used in message IDs (default: random)")

    receive = commands.add_parser("receive", help="receive transfers until stopped")
    common(receive)
    receive.add_argument("--expect", type=int,
                         help="number of messages in the transfer; stop LINGER "
                              "seconds after the last one arrives")
    receive.add_argument("--linger", type=float, default=DEFAULT_LINGER,
                         help="seconds to keep answering after the last expected "
                              "message (default %(default)s)")
    receive.add_argument("--observe", type=positive_float,
                         help="stop this many seconds after the transfer's first message")
    return parser


def plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def describe_send(summary):
    if summary["mode"] == "besteffort":
        return (f"best-effort: sent {summary['messages']} messages in "
                f"{summary['elapsed_s']:.2f} s (no acknowledgements)")
    name = "stop-and-wait"
    if summary["mode"] == "gobackn":
        name = f"go-back-n (window {summary['window']})"
    if summary["success"]:
        return (f"{name}: {summary['acked']}/{summary['messages']} messages "
                f"acknowledged, {plural(summary['retransmissions'], 'retransmission')}, "
                f"{summary['elapsed_s']:.2f} s")
    return (f"{name}: gave up on message {summary['failed_at']} after "
            f"{plural(summary['retransmissions'], 'retransmission')} in total, "
            f"{summary['elapsed_s']:.2f} s ({summary['acked']} acknowledged before that)")


def describe_receive(summary, reason):
    if not summary.get("transfer"):
        return "received nothing"
    text = f"received {summary['delivered']}"
    if "expected" in summary:
        text += f"/{summary['expected']}"
    text += " messages" + (" in order" if summary["in_order"] else "")
    if summary["duplicates"]:
        text += f", {plural(summary['duplicates'], 'duplicate')}"
    return f"{text} ({reason.replace('_', ' ')})"


def main(argv=None):
    from .host import Host  # imported here: host.py imports this module

    args = build_parser().parse_args(argv)
    for name, minimum in (("count", 1), ("retries", 0), ("expect", 1), ("window", 1)):
        value = getattr(args, name, None)
        if value is not None and value < minimum:
            print(f"error: --{name} must be at least {minimum}", file=sys.stderr)
            return 2
    if getattr(args, "linger", 0) < 0:
        print("error: --linger cannot be negative", file=sys.stderr)
        return 2

    log = create_logger(args.run_id, path=log_path(args.log_dir, args.run_id, args.name))
    try:
        config = load_config(args.config)
        if args.command == "send" and args.to not in config["devices"]:
            raise ConfigError(f"unknown destination {args.to}")
        host = Host(config, args.name, log=log, hop_limit=args.hop_limit)
    except (ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "send":
            transfer = args.transfer_id or new_transfer_id()
            if len(message_id(args.mode, transfer, args.count)) > 64:
                print("error: --transfer-id is too long for the message count", file=sys.stderr)
                return 2
            host.log_start("send")
            if args.mode == "stopwait":
                sender = StopAndWaitSender(host, args.to, args.count, transfer,
                                           args.timeout, args.retries, args.rate)
            elif args.mode == "gobackn":
                sender = GoBackNSender(host, args.to, args.count, transfer, args.window,
                                       args.timeout, args.retries, args.rate)
            else:
                sender = BestEffortSender(host, args.to, args.count, transfer, args.rate)
            summary = sender.run()
            print(describe_send(summary), file=sys.stderr)
            return 1 if summary["success"] is False else 0

        receiver = host.receiver
        receiver.expect, receiver.linger, receiver.observe = args.expect, args.linger, args.observe
        host.log_start("receive")
        reason = None
        try:
            while reason is None:
                packet = host.receive(0.05)
                if packet is not None:
                    reply = host.handle(packet)
                    if reply is not None:
                        host.log(host.name, "dropped_unexpected_ack"
                                 if reply["type"] == "ACK" else "dropped_unexpected_echo", reply)
                reason = receiver.finished()
        except KeyboardInterrupt:
            reason = "stopped"
        summary = receiver.summary()
        host.log(host.name, "receiver_done", reason=reason, **summary)
        print(describe_receive(summary, reason), file=sys.stderr)
        return 1 if summary.get("complete") is False else 0
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
