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

Message types are `PROBE`, `ECHO`, `DATA`, and `ACK`. Addresses are at most 32 characters and `message_id` at most 64.

Serialized packets must not exceed 1,200 bytes. `decode_packet` raises `PacketError` for packets that are empty, oversized, not UTF-8 JSON objects, have missing, extra, or duplicate fields, or have fields of the wrong type or out of range.

## Module Interfaces

- `src.packet`: `encode_packet(packet)`, `decode_packet(data)`, and `validate_packet(packet)`.
- `src.config`: `load_config(path)` returns a validated topology configuration.
- `src.transport`: `create_udp_socket(host, port)`, `send_packet(sock, endpoint, packet)`, and `receive_packet(sock)`.
- `src.host`: Host application behavior.
- `src.router`: Packet forwarding behavior.

## Configuration

`config/topology.json` defines device virtual addresses, UDP endpoints, and configured neighbors. The development configuration contains Host 1 and Router A. The final topology must include two hosts and four routers.

## Testing

Install pytest:

`python -m pip install pytest`

Run tests from the repository root:

`python -m pytest -q`

Tests cover packet encoding and decoding, malformed packets, oversized packets, configuration validation, and UDP communication.

## Track B

The reliable-transfer extension uses numbered messages, acknowledgments, retransmissions, timeouts, and duplicate detection. A sender has at most one unacknowledged message at a time. The receiver delivers messages in order and only once.
