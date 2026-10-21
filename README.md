# eps-mini-internet
# Mini Internet Project

## Overview

This project uses Python 3 and UDP sockets to create a small virtual network with two hosts and four routers. Each device has a virtual address and a UDP endpoint. Hosts communicate by sending packets through routers, which use static forwarding tables to determine where to send packets next.

For the extension, we chose Track B: reliable transfer using a stop-and-wait protocol. This will be implemented after the basic network is working.

## Packet Format

Packets are JSON objects converted into UTF-8 bytes.

| Field | Description |
|---|---|
| `src` | Virtual source address |
| `dst` | Virtual destination address |
| `hop_limit` | Maximum number of remaining hops |
| `type` | Message type |
| `message_id` | ID used to match messages and responses |
| `payload` | Message content |

The supported message types are `PROBE`, `ECHO`, `DATA` and `ACK`.

Packets cannot exceed 1,200 bytes. The packet module checks for missing fields, invalid values, malformed JSON and oversized packets.

## Module Interfaces

- `src/packet.py` – Encodes, decodes and validates packets.
- `src/config.py` – Loads and validates the network configuration.
- `src/transport.py` – Handles sending and receiving packets over UDP.
- `src/host.py` – Sends probes, responds with echoes and handles timeouts.
- `src/router.py` – Forwards packets using static forwarding tables.
- `src/forwarding_checks.py` – Checks hop limits, destinations and next hops.
- `src/conditions.py` – Handles packet loss and delay.
- `src/run_log.py` – Records network events in JSON format.

## Configuration

The network topology is defined in `config/topology.json`.

```text
          +-- routerB --+
host1 -- routerA        routerD -- host2
          +-- routerC --+
```

Each device has a virtual address, UDP endpoint and list of neighbors. Routers also have forwarding tables that specify the next hop for each destination.

The configuration includes settings for packet loss and delay on each link. Invalid configurations are rejected with an error message.

The default configuration sends packets from host1 to host2 through routerB and returns them through routerC.

We also have two separate configurations to test both paths:

- `config/topology_upper.json` – Uses routerB for both directions.
- `config/topology_lower.json` – Uses routerC for both directions.

These configurations can be generated using:

`python -m scripts.make_routes`

## Host

To start host2 and have it respond to probes:

`python -m src.host --name host2`

To send three probes from host1 to host2:

`python -m src.host --name host1 --probe host2 --count 3 --timeout 2`

When a host receives a `PROBE`, it sends back an `ECHO` with the same message ID and payload. The sending host waits for a matching response and reports the round-trip time or a timeout.

## Router

Each router receives packets and uses its forwarding table to determine the next hop.

Before forwarding, the router checks the destination, next hop and hop limit. Packets with invalid forwarding information or an exhausted hop limit are dropped.

The routers also support configurable packet loss and delay, and log events such as receiving, forwarding and dropping packets.

To start a router:

`python -m src.router --name routerA`

Replace `routerA` with `routerB`, `routerC` or `routerD` to start the other routers.

## Running the Network

Each host and router runs as a separate process, so the network requires six terminals.

For the upper path, run the following commands in separate terminals:

```bash
python -m src.router --name routerA --config config/topology_upper.json
python -m src.router --name routerB --config config/topology_upper.json
python -m src.router --name routerC --config config/topology_upper.json
python -m src.router --name routerD --config config/topology_upper.json
python -m src.host --name host2 --config config/topology_upper.json
python -m src.host --name host1 --probe host2 --count 3 --timeout 2 --config config/topology_upper.json
```

To test the lower path, stop the processes and run the same commands using `config/topology_lower.json`.

Host2 can also initiate communication by running host1 as the receiving host.

## Testing

Install pytest if it is not already installed:

`python -m pip install pytest`

Run all tests from the repository root:

`python -m pytest -q`

The tests cover:

- Packet encoding, decoding and validation
- Configuration validation
- UDP communication
- Host probe/echo communication and timeouts
- Router forwarding and hop-limit enforcement
- Packet loss and delay
- Network logging
- Upper and lower routing paths

## Track B

For the second phase, we plan to implement reliable transfer using a stop-and-wait protocol.

The sender will send one message at a time and wait for an acknowledgment before sending the next message. If an acknowledgment is not received within the timeout, the message will be retransmitted.

We also plan to include sequence numbers and duplicate detection so messages are delivered in order without duplicates.

This extension has not been implemented yet.