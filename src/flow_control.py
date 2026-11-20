
"""Receiver-advertised flow control for Go-Back-N transfers."""

import json
import time
from collections import deque

from .reliable import Receiver, message_id


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
