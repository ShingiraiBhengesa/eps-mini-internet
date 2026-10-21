# eps-mini-internet
# Mini Internet Project

## Overview

This project implements a virtual network using Python 3 and UDP sockets. Devices have virtual addresses and configured UDP endpoints. Hosts exchange messages through routers, which forward packets using manually configured forwarding tables.

The extension implements Track B: reliable transfer using a stop-and-wait protocol.

## Packet Format

Packets are JSON objects serialized as UTF-8 bytes.

| Field | Description |
|---|---|
| `src` | Virtual source address |
| `dst` | Virtual destination address |
| `hop_limit` | Remaining hop budget, from 0 to 255 |
| `type` | Message type |
| `message_id` | Identifier for a message and its related logs |
| `payload` | Application payload |

Serialized packets must not exceed 1,200 bytes. Malformed packets, invalid fields, and oversized packets are rejected.

## Module Interfaces

- `src.packet`: `encode_packet(packet)`, `decode_packet(data)`, and `validate_packet(packet)`.
- `src.config`: `load_config(path)` returns a validated topology configuration.
- `src.transport`: `create_udp_socket(host, port)`, `send_packet(sock, endpoint, packet)`, and `receive_packet(sock)`.
- `src.host`: Host application behavior.
- `src.router`: Packet forwarding behavior.

## Configuration

`config/topology.json` defines the topology: two hosts and four routers.

```
          +-- routerB --+
host1 -- routerA        routerD -- host2
          +-- routerC --+
```

| Section | Contents |
|---|---|
| `devices.<name>` | `virtual_address` (must equal the name), `role` (`host` or `router`), `udp_host`, `udp_port`, `neighbors` |
| host only | `gateway`: the host's single neighbor; every outgoing packet goes there |
| router only | `forwarding_table`: maps a destination address to a next-hop neighbor |
| `links[]` | `endpoints` (two neighbors), `loss` (0 to 1), `delay_ms` (>= 0) for loss and delay injection |
| `paths` | `forward` (host1 to host2) and `return` (host2 to host1) device lists |

Forward traffic goes through routerB and return traffic goes through routerC.

`load_config(path)` raises `ConfigError` (a `ValueError`) with the device and field when:

- a field is missing or has the wrong type, or two devices share a UDP endpoint
- a neighbor is unknown or one-way, or a pair of neighbors has no link entry
- a host does not have exactly one neighbor, or its gateway is not that neighbor
- a forwarding entry names an unknown destination or a next hop that is not a neighbor
- following the forwarding tables for a path loops, dead-ends, or does not match `paths.forward` or `paths.return`

`get_endpoint(config, name)` returns a device's `(host, port)` and `get_link(config, a, b)` returns the link between two devices.

## Testing

Install pytest:

`python -m pip install pytest`

Run tests from the repository root:

`python -m pytest -q`

Tests cover packet encoding and decoding, malformed packets, oversized packets, configuration validation, and UDP communication.

## Track B

The reliable-transfer extension uses numbered messages, acknowledgments, retransmissions, timeouts, and duplicate detection. A sender has at most one unacknowledged message at a time. The receiver delivers messages in order and only once.
