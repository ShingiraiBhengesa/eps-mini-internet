
"""Receiver-advertised flow control for Go-Back-N transfers."""

import json
import time
from collections import deque

from .reliable import GoBackNSender, Receiver, message_id, parse_message_id


def parse_window_advertisement(payload):
    """Return the available slots and version from a valid ACK payload."""
    try:
        info = json.loads(payload)
    except (TypeError, ValueError):
        return None

    if not isinstance(info, dict) or set(info) != {"rwnd", "version"}:
        return None

    slots = info["rwnd"]
    version = info["version"]

    if type(slots) is not int or type(version) is not int:
        return None

    if slots < 0 or version < 0:
        return None

    return slots, version


class WindowReceiver(Receiver):
    """Receive Go-Back-N messages using a bounded application buffer."""

    def __init__(
        self,
        host,
        buffer_size=2,
        consume_interval=0.15,
        clock=time.monotonic,
        **kwargs,
    ):
        if type(buffer_size) is not int or buffer_size < 1:
            raise ValueError("buffer_size must be a positive integer")

        if consume_interval <= 0:
            raise ValueError("consume_interval must be positive")

        super().__init__(host, clock=clock, **kwargs)

        self.buffer_size = buffer_size
        self.consume_interval = consume_interval
        self.buffers = {}

    def _entry(self, state):
        """Get the buffer associated with a particular transfer."""
        key = (state.src, state.mode, state.transfer)

        if key not in self.buffers:
            self.buffers[key] = {
                "pending": deque(),
                "last_consumed": self.clock(),
                "last_packet": None,
                "version": 0,
            }

        return self.buffers[key]

    def on_data(self, packet):
        """Process pending deliveries before accepting another packet."""
        self.tick()
        super().on_data(packet)

    def _go_back_n(self, state, packet, seq):
        """Buffer the next expected message if space is available."""
        entry = self._entry(state)
        entry["last_packet"] = packet

        pending = entry["pending"]
        highest = state.expected - 1

        if seq == state.expected and len(pending) < self.buffer_size:
            if not pending:
                entry["last_consumed"] = self.clock()

            pending.append(seq)
            state.expected += 1
            highest = seq

            self.log(
                "buffered",
                packet,
                seq=seq,
                buffered=len(pending),
                capacity=self.buffer_size,
            )

        elif seq == state.expected:
            self.log(
                "receiver_buffer_full",
                packet,
                seq=seq,
                buffered=len(pending),
                capacity=self.buffer_size,
            )

        elif seq < state.expected:
            state.duplicates += 1
            self.log(
                "duplicate",
                packet,
                seq=seq,
                acked=True,
                acked_through=highest,
            )

        else:
            self.log(
                "dropped_out_of_order",
                packet,
                seq=seq,
                expected=state.expected,
            )

        if highest:
            self._ack_through(packet, state, highest)

    def _ack_through(self, packet, state, highest):
        """Send a cumulative ACK containing the available buffer space."""
        entry = self._entry(state)
        entry["version"] += 1

        available = self.buffer_size - len(entry["pending"])

        payload = json.dumps(
            {"rwnd": available, "version": entry["version"]},
            separators=(",", ":"),
        )

        ack_id = message_id("gobackn", state.transfer, highest)

        ack = self.host._make_packet(
            "ACK", state.src, ack_id, payload
        )
        self.host._send(ack)

        self.log(
            "window_advertised",
            packet,
            rwnd=available,
            version=entry["version"],
            acked_through=highest,
        )

    def tick(self):
        """Consume buffered messages and advertise newly available space."""
        now = self.clock()

        for state in self.transfers.values():
            if state.mode != "gobackn":
                continue

            entry = self._entry(state)
            pending = entry["pending"]

            if not pending:
                continue

            elapsed = now - entry["last_consumed"]
            due = int(elapsed / self.consume_interval)
            count = min(due, len(pending))

            if count <= 0:
                continue

            for _ in range(count):
                seq = pending.popleft()
                state.delivered.append(seq)

                self.log(
                    "delivered",
                    entry["last_packet"],
                    seq=seq,
                    mode="gobackn",
                )

            if pending:
                entry["last_consumed"] += (
                    count * self.consume_interval
                )
            else:
                entry["last_consumed"] = now

            self._ack_through(
                entry["last_packet"],
                state,
                state.expected - 1,
            )

            if (
                self.expect is not None
                and state.completed_at is None
                and len(state.delivered) >= self.expect
            ):
                state.completed_at = now
                self.log(
                    "transfer_received",
                    transfer=state.transfer,
                    mode=state.mode,
                    expected=self.expect,
                    linger_s=self.linger,
                )


class FlowControlledSender(GoBackNSender):
    """Go-Back-N sender that respects the receiver's advertised window."""

    def __init__(self, *args, max_window_probes=20, **kwargs):
        super().__init__(*args, **kwargs)

        # Send one initial packet to learn the receiver's available space.
        self.advertised_window = 1
        self.window_version = -1

        self.max_window_probes = max_window_probes
        self.window_probes = 0
        self.zero_window_events = 0
        self.window_updates = 0
        self.max_in_flight = 0

    def _receive_ack(self, wait):
        """Read an ACK and update the receiver's advertised capacity."""
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

        if (
            reply["src"] != self.dst
            or not parsed
            or parsed[:2] != (self.mode, self.transfer)
        ):
            self.log(
                "ack_ignored",
                reply,
                reason="an ACK for another transfer",
            )
            return None

        seq = parsed[2]

        if seq > self.highest_sent:
            self.log("ack_ignored", reply, reason="an ACK for unsent data")
            return None

        advertised = parse_window_advertisement(reply["payload"])

        if advertised is None:
            self.log(
                "ack_ignored",
                reply,
                reason="missing or invalid receiver window",
            )
            return None

        free, version = advertised

        # Ignore stale advertisements that arrive out of order.
        if version > self.window_version:
            previous = self.advertised_window
            self.window_version = version
            self.advertised_window = min(free, self.window)
            self.window_updates += 1

            if previous > 0 and self.advertised_window == 0:
                self.zero_window_events += 1

            self.log(
                "window_updated",
                reply,
                rwnd=free,
                effective_window=self.advertised_window,
                version=version,
            )

        if seq < self.base:
            self.log(
                "ack_ignored",
                reply,
                reason=f"a duplicate ACK for #{seq}",
            )
            return None

        rtt_ms = (self.clock() - self.sent_at[seq]) * 1000

        self.log(
            "ack_received",
            reply,
            seq=seq,
            acked_through=seq,
            newly_acked=seq - self.base + 1,
            attempt=self.attempts[seq],
            rtt_ms=round(rtt_ms, 3),
        )

        return seq

    def run(self):
        """Transfer messages without exceeding the advertised window."""
        self.log(
            "transfer_started",
            mode=self.mode,
            transfer=self.transfer,
            dst=self.dst,
            messages=self.count,
            window=self.window,
            rate=self.rate,
            timeout_s=self.timeout,
            retries=self.retries,
            flow_control=True,
        )

        start = self.clock()
        timer = None
        stalls = 0
        next_probe = None
        probes_without_progress = 0

        while self.base <= self.count:

            # Retransmit unacknowledged packets when their timer expires.
            if timer is not None and self.clock() >= timer + self.timeout:
                stalls += 1

                self.log(
                    "ack_timeout",
                    self._packet(self.base),
                    seq=self.base,
                    attempt=self.attempts[self.base],
                    timeout_s=self.timeout,
                    in_flight=self.next_seq - self.base,
                )

                if stalls > self.retries:
                    self.log(
                        "transfer_failed",
                        self._packet(self.base),
                        seq=self.base,
                        attempts=self.attempts[self.base],
                    )
                    return self._finish(
                        start,
                        self.base - 1,
                        failed_at=self.base,
                    )

                self.next_seq = self.base
                timer = None
                continue

            # Both the sender window and receiver window limit transmission.
            limit = min(self.window, self.advertised_window)

            room = (
                self.next_seq <= self.count
                and self.next_seq < self.base + limit
            )

            if room and self.pacer.ready_in() == 0:
                self._send(self.next_seq)

                if self.next_seq == self.base:
                    timer = self.sent_at[self.base]

                self.next_seq += 1

                self.max_in_flight = max(
                    self.max_in_flight,
                    self.next_seq - self.base,
                )

                next_probe = None
                continue

            waits = []

            if timer is not None:
                waits.append(
                    max(0, timer + self.timeout - self.clock())
                )

            if room:
                waits.append(self.pacer.ready_in())

            # Probe a closed window if an update might have been lost.
            if (
                not waits
                and self.advertised_window == 0
                and self.base > 1
            ):
                if next_probe is None:
                    next_probe = self.clock() + self.timeout

                remaining = next_probe - self.clock()

                if remaining <= 0:
                    if probes_without_progress >= self.max_window_probes:
                        self.log(
                            "transfer_failed",
                            self._packet(self.base),
                            seq=self.base,
                            reason="zero_window_timeout",
                        )
                        return self._finish(
                            start,
                            self.base - 1,
                            failed_at=self.base,
                        )

                    # Resend acknowledged DATA to request a fresh window.
                    packet = self._packet(self.base - 1)
                    self.pacer.wait()
                    self.host._send(packet)

                    self.window_probes += 1
                    probes_without_progress += 1

                    self.log(
                        "window_probe",
                        packet,
                        seq=self.base - 1,
                        attempt=probes_without_progress,
                    )

                    next_probe = self.clock() + self.timeout
                    continue

                waits.append(remaining)

            elif not waits:
                waits.append(self.timeout)

            wait = min(waits)

            if wait <= 0:
                continue

            acked = self._receive_ack(wait)

            if acked is not None:
                self.base = acked + 1
                self.next_seq = max(self.next_seq, self.base)
                stalls = 0
                probes_without_progress = 0

                timer = (
                    self.clock()
                    if self.base < self.next_seq
                    else None
                )

            if self.advertised_window > 0:
                next_probe = None
                probes_without_progress = 0

        return self._finish(start, self.count)

    def _finish(self, start, acked, failed_at=None):
        """Record flow-control metrics in the transfer summary."""
        summary = {
            "mode": self.mode,
            "transfer": self.transfer,
            "window": self.window,
            "messages": self.count,
            "acked": acked,
            "retransmissions": self.retransmissions,
            "elapsed_s": round(self.clock() - start, 3),
            "success": failed_at is None,
            "failed_at": failed_at,
            "flow_control": True,
            "window_updates": self.window_updates,
            "zero_window_events": self.zero_window_events,
            "window_probes": self.window_probes,
            "max_in_flight": self.max_in_flight,
        }

        self.log("transfer_done", **summary)
        return summary
