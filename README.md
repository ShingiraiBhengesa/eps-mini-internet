# eps-mini-internet
# Mini Internet Project

## Overview

This project uses Python 3 and UDP sockets to create a small virtual network with two hosts and four routers. Each device has a virtual address and a UDP endpoint. Hosts communicate by sending packets through routers, which use static forwarding tables to determine where to send packets next.

For the extension, we chose Track B: reliable transfer using a stop-and-wait protocol (see [Track B](#track-b)).

## Setup

Requires Python 3.10 or newer (developed and tested on Python 3.13 on macOS). From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

`requirements.txt` lists `pytest` (tests) and `matplotlib` (plots). The network itself uses only the standard library. Every command below assumes the virtual environment is active and is run from the repository root.

## Packet Format

Packets are JSON objects encoded as UTF-8 bytes (compact separators, ASCII only). A packet has exactly these six fields: missing fields, extra fields and repeated fields are all rejected.

| Field | Type and limits | Description |
|---|---|---|
| `src` | nonempty string, at most 32 characters | Virtual source address |
| `dst` | nonempty string, at most 32 characters | Virtual destination address |
| `hop_limit` | integer from 0 to 255 | Remaining hops (see [Forwarding](#forwarding)) |
| `type` | one of `PROBE`, `ECHO`, `DATA`, `ACK` | Message type |
| `message_id` | nonempty string, at most 64 characters | Matches a response to its request |
| `payload` | string | Message content |

An encoded packet cannot exceed 1,200 bytes. `src/packet.py` checks every packet on send and on receive and rejects malformed JSON, wrong types and oversized packets.

Probe IDs have the form `<host>-<session>-<n>`, for example `host1-3a3e3393-2` for the second probe of a run. The random session part stops a late echo from an earlier run from matching a new probe. An `ECHO` reuses its probe's `message_id` and `payload`.

## Forwarding

Each router handles a received packet in this order. The first check that fails drops the packet and logs a `drop` event with the reason shown.

1. The packet must decode and validate. Otherwise: `malformed_packet`.
2. It must arrive from the UDP endpoint of one of the router's configured neighbors. Otherwise: `unconfigured_sender`.
3. **Hop limit:** if the incoming `hop_limit` is 1 or less, the packet is dropped (`hop_limit_exhausted`). Otherwise the router subtracts one and forwards it.
4. The destination must have an entry in the forwarding table. Otherwise: `unknown_destination`.
5. The next hop must be a neighbor. Otherwise: `invalid_next_hop`.

Hosts send with `hop_limit` 16 by default (`--hop-limit` changes it), so a probe from host1 reaches host2 through three routers with `hop_limit` 13.

## Network Conditions

Every link in the config has a loss rate (`loss`, 0 to 1) and a one-way delay (`delay_ms`). Both directions of a link use the same entry. The router that sends on a link applies it, in this order:

1. **Forced drop rules** (`--drop-rule`, below). A forced drop is logged as `drop_forced` and does not use a random draw.
2. **Random loss**, from the router's own random generator seeded with `--seed`. A random drop is logged as `drop_injected`.
3. **Delay**: the packet is queued and sent once its delay has passed. The router keeps receiving and forwarding other packets in the meantime.

**Hosts send straight to their gateway router without loss or delay.** So host1→routerA and host2→routerD are never lossy or delayed, but routerA→host1 and routerD→host2 can be.

All links in the committed configs have 0 loss and 0 ms delay. Experiments set loss and delay on one link at run time (see [Experiments](#experiments)).

### Forced drop rules

A rule drops chosen packets a fixed number of times, so a test can lose one specific packet instead of relying on chance:

```bash
python -m src.router --name routerB --drop-rule "type=PROBE,skip=1"
```

A rule matches on any of `message_id`, `type`, `src`, `dst` and `next_hop` (the neighbor the packet is about to be sent to). `skip=N` lets the first N matching packets through, and `count=N` drops the next N (default 1). After that, matching packets go through again, so a retransmission with the same ID is delivered. `--drop-rule` can be given more than once.

## Logging

Every host and router writes one JSON object per event, both to the terminal and to `logs/<run-id>/<device>.jsonl`:

```bash
python -m src.router --name routerA --run-id trial-3
# writes logs/trial-3/routerA.jsonl
```

`--run-id` defaults to `default` and may use letters, digits, `.`, `_` and `-`. `--log-dir` changes the `logs` folder. Every record has `run_id`, `device`, `timestamp` and `event`, plus the packet's `message_id`, `type`, `src`, `dst` and `hop_limit` when the event concerns a packet. Records with the same `run_id` and `message_id` follow one packet across devices.

Each process logs a `started` record first. A router's records its UDP endpoint, its seed, the loss and delay of every link it sends on, and its drop rules. A host's records its endpoint, gateway, hop limit and mode.

| Event | Device | Meaning |
|---|---|---|
| `started` | all | Process is ready, with its settings |
| `sent`, `received` | all | A packet left or arrived |
| `forward`, `send_scheduled` | router | Forwarding decision made; packet queued for its link delay |
| `drop` | router | Forwarding check failed; `reason` says which |
| `drop_injected` | router | Random loss on the outgoing link |
| `drop_forced` | router | A `--drop-rule` matched; `rule` says which |
| `echo_received` | host | Matching echo, with `rtt_ms` |
| `probe_timeout` | host | No matching echo within `--timeout` |
| `echo_payload_mismatch` | host | Echo had the right ID but a different payload; it does not count as an answer |
| `dropped_unexpected_echo` | host | Echo for another probe, for example a late one |

The `logs/` folder is ignored by git. Experiment logs go to `results/` instead and are committed.

## Configuration

The network topology is defined in `config/topology.json`.

```text
          +-- routerB --+
host1 -- routerA        routerD -- host2
          +-- routerC --+
```

| Device | UDP endpoint |
|---|---|
| host1 | 127.0.0.1:5001 |
| host2 | 127.0.0.1:5002 |
| routerA | 127.0.0.1:5101 |
| routerB | 127.0.0.1:5102 |
| routerC | 127.0.0.1:5103 |
| routerD | 127.0.0.1:5104 |

Each device has a virtual address, UDP endpoint and list of neighbors. Routers also have forwarding tables that specify the next hop for each destination. The `paths` section lists the expected host1→host2 and host2→host1 routes, and the validator rejects a config whose forwarding tables don't produce them, including any loop on those routes. Invalid configurations are rejected with an error message.

| File | Routes |
|---|---|
| `config/topology.json` | host1→host2 through routerB, replies through routerC |
| `config/topology_upper.json` | routerB in both directions |
| `config/topology_lower.json` | routerC in both directions |
| `config/topology_loop.json` | as `topology.json`, plus a forwarding loop for packets addressed to routerD |

The last three are generated from `config/topology.json` by:

```bash
python -m scripts.make_routes
```

## Running the Network by Hand

Each host and router runs as a separate process. For the upper path, run each command in its own terminal:

```bash
python -m src.router --name routerA --config config/topology_upper.json
python -m src.router --name routerB --config config/topology_upper.json
python -m src.router --name routerC --config config/topology_upper.json
python -m src.router --name routerD --config config/topology_upper.json
python -m src.host --name host2 --config config/topology_upper.json
python -m src.host --name host1 --probe host2 --count 3 --timeout 2 --config config/topology_upper.json
```

Stop the routers and host2 with Ctrl-C. The probing host exits on its own and prints `N/M probes answered` to standard error. Its exit code is 0 only if every probe was answered. Add the same `--run-id` to every command to group the logs. For the lower path, use `config/topology_lower.json`. Host2 can also probe host1 by swapping the roles.

### Live demo view (two terminals)

`scripts.demo_net` starts the four routers and host2 in one terminal and prints every device's events as readable lines as they happen:

```bash
# terminal 1: leave this on screen
python -m scripts.demo_net --run-id demo

# terminal 2: send traffic
python -m src.host --name host1 --probe host2 --count 3 --config config/topology_upper.json --run-id demo
```

```text
14:43:21.277  host1    sends PROBE host1→host2 to routerA (hop limit 16)  [host1-10510c31-1]
14:43:21.277  routerA  forwards PROBE host1→host2 to routerB, hop limit now 15  [host1-10510c31-1]
...
14:43:21.278  host1    gets the ECHO back: round trip 1.3 ms  [host1-10510c31-1]
```

It uses the upper path by default and takes the same `--config`, `--seed`, `--link/--loss/--delay-ms` and `--drop-rule ROUTER:RULE` options as the launcher. `--all` also shows each router's receive, queue and send steps. Ctrl-C stops the whole network. The logs stay in `logs/<run-id>/`, and reusing a run ID needs `--replace`, which deletes the old folder.

### Tracing one packet

```bash
python -m scripts.trace logs/demo        # every probe in the run and how it ended
python -m scripts.trace logs/demo 2      # probe 2, hop by hop, with timings
```

The trace lists each step with its time since the probe was sent, then the whole path, for example `host1 → routerA → routerB → routerD → host2 → routerD → routerB → routerA → host1`. A lost packet ends at the router that dropped it, with the reason. It works on any run folder, including those under `results/`.

## Demo Scenarios

Each demo starts the six processes above unless it says otherwise. Every expected log line below was produced by running the command.

**Probe and echo.** host2 answers each `PROBE` with an `ECHO` carrying the same message ID and payload. host1 accepts an echo only if the ID, the sender and the payload all match, then logs `echo_received` with `rtt_ms`. An echo whose payload differs is logged as `echo_payload_mismatch` and ignored. By default probes carry `probe-0001`, `probe-0002`, and so on (always 10 bytes). `--payload` sets a fixed payload instead.

**Forwarding loop.** Start the four routers with `--config config/topology_loop.json`, then:

```bash
python -m src.host --name host1 --probe routerD --hop-limit 6 --config config/topology_loop.json
```

routerA and routerB send packets for routerD to each other, so the probe bounces until its hop limit runs out:

```text
routerA forward hop_limit 5 -> routerB
routerB forward hop_limit 4 -> routerA
routerA forward hop_limit 3 -> routerB
routerB forward hop_limit 2 -> routerA
routerA forward hop_limit 1 -> routerB
routerB drop    reason hop_limit_exhausted
```

The validator rejects loops on the host1↔host2 routes in `paths`, so the loop uses routerD as the destination instead. Traffic between the hosts is routed normally in this config.

**Unknown destination.** routerB is a device in the config, but no router has a forwarding entry for it:

```bash
python -m src.host --name host1 --probe routerB --timeout 1
```

routerA logs `"event":"drop" ... "reason":"unknown_destination"` and the probe times out. A name that isn't in the config at all, such as `hostZ`, is rejected by the host before anything is sent.

**Invalid packet.** Send bytes that aren't a packet to routerA's port:

```bash
python -c "import socket; socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b'not a packet', ('127.0.0.1', 5101))"
```

routerA logs `"event":"drop","reason":"malformed_packet","detail":"Invalid JSON packet"` and keeps running.

**Forced drop of one packet.** Start routerB with a drop rule, using the upper path so probes cross routerB:

```bash
python -m src.router --name routerB --config config/topology_upper.json --drop-rule "type=PROBE,skip=1"
python -m src.host --name host1 --probe host2 --count 3 --interval 0.2 --timeout 0.5 --config config/topology_upper.json
```

routerB logs `drop_forced` for the second probe only. host1 logs `echo_received`, `probe_timeout`, `echo_received` and prints `2/3 probes answered`.

## Experiments

### Tools

| Command | What it does |
|---|---|
| `python -m scripts.launch ...` | One run: starts the 4 routers and host2, runs host1's probes, stops everything |
| `python -m scripts.run_baseline` | The whole baseline matrix (12 runs) |
| `python -m scripts.metrics <folder>` | Reads every run's logs; prints a table, writes `runs.csv` and `conditions.csv` |
| `python -m scripts.plot_results <folder>` | Draws `baseline.png` from those results |

Example single run:

```bash
python -m scripts.launch --config config/topology_upper.json --run-id demo-1 --seed 1 \
    --link routerB-routerD --loss 0.05 --delay-ms 20 --count 20 --interval 0.2 --timeout 1.0
```

Each run gets a folder holding the items below. It's `results/manual/demo-1/` here: single runs go under `results/manual/`, which git ignores, unless `--out` names another folder.

- `config.json`: the exact topology used, with the link change applied
- `run.json`: seed, router seeds, parameters, start and end times, the sender's result, and the commit the code ran from
- one `<device>.jsonl` log per process

The launcher refuses to overwrite an existing run folder. The run seed sets each router's seed as `seed × 10 + i` (routerA = +0 to routerD = +3), so routers don't share a random sequence. `--drop-rule routerB:type=PROBE,skip=1` adds a forced drop rule to one router.

### Baseline

```bash
python -m scripts.run_baseline
python -m scripts.plot_results results/baseline
```

**Setup.** Upper path (`config/topology_upper.json`), so probes and echoes both cross the **routerB–routerD** link, the only link with loss or delay. The test varies 2 one-way delays (20 ms, 80 ms) × 2 loss rates (0%, 5%), 3 runs per condition, 20 probes per run, with seeds 1 to 12, one per run. These stay fixed: path, 10-byte payloads, 0.2 s between probes, and a 1 s timeout, which is about 6 times the slowest expected round trip. The raw logs are in `results/baseline/<run-id>/`.

**Results** (`results/baseline/conditions.csv`; RTT pooled over all answered probes):

| Condition | Probes | Answered | Success (± SD across runs) | RTT mean ± SD | RTT median | Timeouts |
|---|---|---|---|---|---|---|
| 20 ms, 0% loss | 60 | 60 | 100.0% (± 0.0) | 58.4 ± 95.1 ms | 46.2 ms | 0 |
| 20 ms, 5% loss | 60 | 56 | 93.3% (± 2.9) | 45.8 ± 2.3 ms | 45.7 ms | 4 |
| 80 ms, 0% loss | 60 | 60 | 100.0% (± 0.0) | 166.5 ± 4.7 ms | 165.9 ms | 0 |
| 80 ms, 5% loss | 60 | 52 | 86.7% (± 10.4) | 166.2 ± 1.5 ms | 166.4 ms | 8 |

![Baseline results](results/baseline/baseline.png)

**Reading the results.**

- **Round-trip time** is twice the link delay plus about 6 ms (see the medians). The 6 ms is the cost of six router hops (three each way) in Python, including writing every log line. Loss doesn't change the round-trip time of the probes that get through.
- **One outlier.** Probe 13 of `baseline-d20-l0-s2` took 782 ms. Its log trace shows 470 ms passing inside routerA between `received` and `forward`, steps that normally take well under a millisecond, and routerD stalled briefly at the same moment. That points to the computer pausing the processes, not to the configured delay or the forwarding code. The probe is kept: it raises that condition's mean to 58.4 ms and SD to 95.1 ms, while its median (46.2 ms) matches the 20 ms, 5% loss condition. Smaller pauses of the same kind explain the 80 ms, 0% loss SD of 4.7 ms (two probes at 180 and 199 ms).
- **Loss** applies in both directions, so a probe succeeds with probability 0.95² ≈ 90.25%. Across both delay settings, 12 of 120 lossy probes failed (90.0% success). The gap between 93.3% and 86.7% comes from 20-probe samples, not from the delay: one 80 ms run (seed 11) lost 5 of 20.
- **Every timeout matches exactly one `drop_injected` event** on routerB or routerD, and no run was incomplete. Repeating the matrix with the same seeds lost exactly the same probes in every run.

## Testing

```bash
python -m pytest -q
```

The tests cover:

- Packet encoding, decoding and validation
- Configuration validation and the generated route configs
- UDP communication
- Host probe/echo communication, payload checks and timeouts
- Router forwarding, hop-limit enforcement and the forwarding loop
- Packet loss, delay and forced drop rules
- Network logging, log files and run IDs
- Upper and lower routing paths
- The launcher (including a full run on spare ports) and the metrics
- The live demo view and the packet trace

## Module Interfaces

- `src/packet.py`: Encodes, decodes and validates packets.
- `src/config.py`: Loads and validates the network configuration.
- `src/transport.py`: Handles sending and receiving packets over UDP.
- `src/host.py`: Sends probes, responds with echoes and handles timeouts.
- `src/router.py`: Forwards packets using static forwarding tables.
- `src/forwarding_checks.py`: Checks hop limits, destinations and next hops.
- `src/conditions.py`: Applies loss, delay and forced drop rules on outgoing links.
- `src/run_log.py`: Records network events as JSON lines, to the terminal and to per-device files.
- `src/shutdown.py`: Stops a process cleanly on Ctrl-C or SIGTERM.
- `scripts/make_routes.py`: Generates the upper, lower and loop configs.
- `scripts/launch.py`, `scripts/run_baseline.py`, `scripts/metrics.py`, `scripts/plot_results.py`: Experiment tools.
- `scripts/demo_net.py`, `scripts/trace.py`: Live demo view and packet tracing.

## Track B

For the second phase, we plan to implement reliable transfer using a stop-and-wait protocol.

The sender will send one message at a time and wait for an acknowledgment before sending the next message. If an acknowledgment is not received within the timeout, the message will be retransmitted.

We also plan to include sequence numbers and duplicate detection so messages are delivered in order without duplicates.

This extension has not been implemented yet.
