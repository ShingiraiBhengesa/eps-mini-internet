
import heapq
import random
import time

from .config import get_endpoint, get_link
from .transport import send_packet


class LinkScheduler:
    def __init__(self, config, device, sock, seed=0, log=None, clock=None, sender=None):
        if device not in config["devices"]:
            raise ValueError(f"Unknown device: {device}")

        self.config = config
        self.device = device
        self.sock = sock
        self.seed = seed
        self.random = random.Random(seed)
        self.log = log if log is not None else lambda *args, **kwargs: None
        self.clock = clock if clock is not None else time.monotonic
        self.sender = sender if sender is not None else send_packet
        self.pending = []
        self.sequence = 0

    def enqueue(self, next_hop, packet):
        neighbors = self.config["devices"][self.device]["neighbors"]

        if next_hop not in neighbors:
            raise ValueError(f"{next_hop} is not a neighbor of {self.device}")

        link = get_link(self.config, self.device, next_hop)
        loss = link["loss"]
        delay_ms = link["delay_ms"]

        if self.random.random() < loss:
            self.log(
                self.device, "drop_injected", packet,
                next_hop=next_hop, loss=loss, seed=self.seed
            )
            return False

        due = self.clock() + delay_ms / 1000.0
        heapq.heappush(
            self.pending,
            (due, self.sequence, next_hop, dict(packet))
        )
        self.sequence += 1

        self.log(
            self.device, "send_scheduled", packet,
            next_hop=next_hop, delay_ms=delay_ms
        )
        return True

    def send_ready(self):
        sent = 0

        while self.pending and self.pending[0][0] <= self.clock():
            _, _, next_hop, packet = heapq.heappop(self.pending)
            endpoint = get_endpoint(self.config, next_hop)
            self.sender(self.sock, endpoint, packet)
            self.log(self.device, "sent", packet, next_hop=next_hop)
            sent += 1

        return sent

    def next_timeout(self, maximum=0.2):
        if not self.pending:
            return maximum

        remaining = max(0.0, self.pending[0][0] - self.clock())
        return min(maximum, remaining)
