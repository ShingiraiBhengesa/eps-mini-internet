import heapq
import random
import time

from .config import get_endpoint, get_link
from .transport import send_packet

# Packet fields a rule can match on.
DROP_RULE_FIELDS = (
    "message_id", "type", "src", "dst", "next_hop"
)


class DropRule:
    """Drop chosen packets a fixed number of times."""

    def __init__(self, count=1, skip=0, **match):
        unknown = sorted(set(match) - set(DROP_RULE_FIELDS))
        if unknown:
            raise ValueError(
                f"unknown drop rule field {unknown[0]!r}; use "
                + ", ".join(DROP_RULE_FIELDS + ("skip", "count"))
            )
        if not match:
            raise ValueError(
                "a drop rule must match at least one packet field"
            )
        if type(count) is not int or count < 1:
            raise ValueError(
                "count must be a whole number of at least 1"
            )
        if type(skip) is not int or skip < 0:
            raise ValueError(
                "skip must be a whole number of at least 0"
            )

        self.match = dict(match)
        self.count = count
        self.skip = skip
        self.seen = 0

    @classmethod
    def parse(cls, text):
        """Build a rule from text such as 'type=ACK,count=1'."""
        fields = {}

        for part in text.split(","):
            key, sep, value = part.strip().partition("=")
            key, value = key.strip(), value.strip()

            if not sep or not key or not value:
                raise ValueError(
                    f"bad drop rule part {part.strip()!r}; "
                    "expected field=value"
                )

            if key in fields:
                raise ValueError(
                    f"drop rule repeats field {key!r}"
                )

            fields[key] = value

        for key in ("count", "skip"):
            if key in fields:
                try:
                    fields[key] = int(fields[key])
                except ValueError:
                    raise ValueError(
                        f"{key} must be a whole number"
                    ) from None

        return cls(**fields)

    def check(self, packet, next_hop):
        """Return True when a matching packet should be affected."""
        values = dict(packet, next_hop=next_hop)

        if any(
            values.get(key) != value
            for key, value in self.match.items()
        ):
            return False

        self.seen += 1
        return self.skip < self.seen <= self.skip + self.count

    def describe(self):
        parts = [
            f"{key}={value}"
            for key, value in self.match.items()
        ]
        parts += [
            f"skip={self.skip}",
            f"count={self.count}"
        ]
        return ",".join(parts)


class CorruptRule(DropRule):
    """Corrupt chosen packets a fixed number of times.

    Uses the same matching, skip, and count behavior as DropRule,
    but corrupts the packet instead of dropping it.
    """


class LinkScheduler:
    """Apply outgoing link loss, corruption, and delay.

    Forced drops and random loss are checked first.
    Corruption rules are applied to surviving packets.
    Corruption is injected when the packet is transmitted.
    """

    def __init__(
        self, config, device, sock,
        seed=0, log=None, clock=None, sender=None,
        drop_rules=None, corrupt_rules=None
    ):
        if device not in config["devices"]:
            raise ValueError(f"Unknown device: {device}")

        self.config = config
        self.device = device
        self.sock = sock
        self.seed = seed
        self.random = random.Random(seed)
        self.log = log if log is not None else (
            lambda *args, **kwargs: None
        )
        self.clock = (
            clock if clock is not None else time.monotonic
        )
        self.sender = (
            sender if sender is not None else send_packet
        )

        self.drop_rules = list(drop_rules or [])
        self.corrupt_rules = list(corrupt_rules or [])

        self.pending = []
        self.sequence = 0

    def link_settings(self):
        """Loss and delay of every link this device sends on."""
        settings = []

        for neighbor in self.config["devices"][self.device]["neighbors"]:
            link = get_link(self.config, self.device, neighbor)

            settings.append({
                "neighbor": neighbor,
                "loss": link["loss"],
                "delay_ms": link["delay_ms"],
            })

        return settings

    def enqueue(self, next_hop, packet):
        neighbors = self.config["devices"][self.device]["neighbors"]

        if next_hop not in neighbors:
            raise ValueError(
                f"{next_hop} is not a neighbor of {self.device}"
            )

        # Check forced packet drop rules.
        fired = [
            rule for rule in self.drop_rules
            if rule.check(packet, next_hop)
        ]

        if fired:
            self.log(
                self.device, "drop_forced", packet,
                next_hop=next_hop,
                rule=fired[0].describe()
            )
            return False

        link = get_link(self.config, self.device, next_hop)
        loss = link["loss"]
        delay_ms = link["delay_ms"]

        # Check random packet loss.
        if self.random.random() < loss:
            self.log(
                self.device, "drop_injected", packet,
                next_hop=next_hop,
                loss=loss,
                seed=self.seed
            )
            return False

        # Check forced corruption rules.
        corrupted = [
            rule for rule in self.corrupt_rules
            if rule.check(packet, next_hop)
        ]

        should_corrupt = bool(corrupted)

        if should_corrupt:
            self.log(
                self.device, "corrupt_scheduled", packet,
                next_hop=next_hop,
                rule=corrupted[0].describe()
            )

        # Schedule the packet with its corruption status.
        due = self.clock() + delay_ms / 1000.0

        heapq.heappush(
            self.pending,
            (
                due,
                self.sequence,
                next_hop,
                dict(packet),
                should_corrupt
            )
        )

        self.sequence += 1

        self.log(
            self.device, "send_scheduled", packet,
            next_hop=next_hop,
            delay_ms=delay_ms
        )

        return True

    def send_ready(self):
        sent = 0

        while self.pending and self.pending[0][0] <= self.clock():
            _, _, next_hop, packet, corrupt = heapq.heappop(
                self.pending
            )

            endpoint = get_endpoint(self.config, next_hop)

            if corrupt:
                # Encode the packet, then corrupt its serialized data.
                self.sender(
                    self.sock,
                    endpoint,
                    packet,
                    corrupt=True
                )

                self.log(
                    self.device, "corrupt_injected", packet,
                    next_hop=next_hop
                )
            else:
                # Preserve compatibility with existing sender functions.
                self.sender(self.sock, endpoint, packet)

            self.log(
                self.device, "sent", packet,
                next_hop=next_hop
            )

            sent += 1

        return sent

    def next_timeout(self, maximum=0.2):
        if not self.pending:
            return maximum

        remaining = max(
            0.0,
            self.pending[0][0] - self.clock()
        )

        return min(maximum, remaining)
    