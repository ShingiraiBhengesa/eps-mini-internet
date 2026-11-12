# eps-mini-internet
# Mini Internet Project

## Overview

This project uses Python 3 and UDP sockets to create a small virtual network with two hosts and four routers. Each device has a virtual address and a UDP endpoint. Hosts communicate by sending packets through routers, which use static forwarding tables to determine where to send packets next.

For the extension, we chose Track B: reliable transfer using a stop-and-wait protocol (see [Track B](#track-b)).

## Quick Start

Requires Python 3.10 or newer. Developed and tested on Python 3.13 on macOS. Everything runs on your own computer over localhost, so no network connection is needed after setup.

### 1. Get the code

First time:

```bash
git clone https://github.com/ShingiraiBhengesa/eps-mini-internet.git
cd eps-mini-internet
```

Already cloned? Update it:

```bash
cd eps-mini-internet
git switch main
git pull
```

### 2. One-time setup

From inside the `eps-mini-internet` folder, create a virtual environment and install the two packages the project uses, `pytest` for tests and `matplotlib` for plots:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows, activate with `.venv\Scripts\activate` instead of `source .venv/bin/activate`.

### 3. In every new terminal

Every command in this README must run **from the project folder** with the **virtual environment active**. Each new terminal window or tab starts outside both, so first run:

```bash
cd ~/eps-mini-internet
source .venv/bin/activate
```

If you cloned the project somewhere other than your home folder, use that path in the `cd` line. Your prompt now starts with `(.venv)`. If you use conda and your prompt also shows `(base)`, that's fine: once `.venv` is active, `python` uses the project's environment.

If you see `No module named 'scripts'` or `No module named 'src'`, you skipped this step: you're in the wrong folder, or the environment isn't active.

### 4. Check that everything works

```bash
python -m pytest -q
```

Expected: `162 passed` in a few seconds.

### 5. Watch the network run

Open two terminals and do step 3 in both.

**Terminal 1** starts the four routers and host2, then shows every event as it happens. Leave it running:

```bash
python -m scripts.demo_net --run-id demo --replace
```

**Terminal 2** sends three probes from host1 to host2:

```bash
python -m src.host --name host1 --probe host2 --count 3 --run-id demo
```

Terminal 2 prints `3/3 probes answered`. Terminal 1 shows each probe crossing the network and the echo coming back:

```text
14:49:33.678  host1    sends PROBE host1→host2 to routerA (hop limit 16)  [host1-3622e93a-1]
14:49:33.678  routerA  forwards PROBE host1→host2 to routerB, hop limit now 15  [host1-3622e93a-1]
14:49:33.679  routerB  forwards PROBE host1→host2 to routerD, hop limit now 14  [host1-3622e93a-1]
14:49:33.679  routerD  forwards PROBE host1→host2 to host2, hop limit now 13  [host1-3622e93a-1]
14:49:33.679  host2    sends ECHO host2→host1 to routerD (hop limit 16)  [host1-3622e93a-1]
14:49:33.679  routerD  forwards ECHO host2→host1 to routerB, hop limit now 15  [host1-3622e93a-1]
14:49:33.679  routerB  forwards ECHO host2→host1 to routerA, hop limit now 14  [host1-3622e93a-1]
14:49:33.679  routerA  forwards ECHO host2→host1 to host1, hop limit now 13  [host1-3622e93a-1]
14:49:33.680  host1    gets the ECHO back: round trip 1.5 ms  [host1-3622e93a-1]
```

Your times and IDs will differ. Then follow one probe hop by hop, still in terminal 2:

```bash
python -m scripts.trace logs/demo 2
```

### 6. Stop

Press **Ctrl-C in terminal 1**. It stops every router and host2 and prints `Network stopped`. The logs stay in `logs/demo/`.

## Common Problems

| You see | Fix |
|---|---|
| `No module named 'scripts'` or `'src'` | Run the two lines from [step 3](#3-in-every-new-terminal) in that terminal. |
| `No module named 'pytest'` or `'matplotlib'` | The environment isn't active (`source .venv/bin/activate`), or setup was skipped ([step 2](#2-one-time-setup)). |
| `exited with code 2 before starting`, or `Address already in use` | An old router or host is still running. Stop it with `pkill -f "src.(router|host)"`, then try again. |
| `... already exists; pick another --run-id or add --replace` | That run ID was used before. Add `--replace`, or pick a new `--run-id`. |
| Terminal 2 prints answers, but terminal 1 shows nothing | Both terminals must use the same `--run-id`. |
| `0/3 probes answered` | The network isn't running. Start terminal 1 first and wait for its `started` lines. |

## Running the Network

### Live view (two terminals)

`scripts.demo_net` is the easiest way to run the network, as in [Quick Start](#5-watch-the-network-run). It starts the four routers and host2 from one terminal and prints readable lines for every device's events, including host1's when host1 uses the same `--run-id`.

| Option | Effect |
|---|---|
| `--run-id NAME` | Name of the run; logs go to `logs/NAME/`. Default `demo`. |
| `--replace` | Delete an earlier run's logs with the same name first. |
| `--config FILE` | Topology to use. Default `config/topology_upper.json` (through routerB both ways). |
| `--link A-B --loss RATE --delay-ms MS` | Add loss (0 to 1) and one-way delay to one link. |
| `--drop-rule ROUTER:RULE` | Make one router drop chosen packets ([forced drop rules](#forced-drop-rules)). |
| `--seed N` | Seed for random loss; router i gets `N × 10 + i`. Default 0. |
| `--all` | Also show each router's receive, queue and send steps. |

host1 only needs its own address and gateway, which every config file shares, so terminal 2 doesn't need `--config`.

### Tracing one packet

List every probe in a run and how it ended:

```bash
python -m scripts.trace logs/demo
```

Follow probe 2 hop by hop, with timings:

```bash
python -m scripts.trace logs/demo 2
```

The trace lists each step with its time since the probe was sent, then the whole path, for example `host1 → routerA → routerB → routerD → host2 → routerD → routerB → routerA → host1`. A lost packet ends at the router that dropped it, with the reason. It works on any run folder, including those under `results/`.

### By hand (six terminals)

Each device can also be started on its own. Do [step 3](#3-in-every-new-terminal) in each terminal, then run one command per terminal, routers and host2 first:

```bash
python -m src.router --name routerA --config config/topology_upper.json
python -m src.router --name routerB --config config/topology_upper.json
python -m src.router --name routerC --config config/topology_upper.json
python -m src.router --name routerD --config config/topology_upper.json
python -m src.host --name host2 --config config/topology_upper.json
python -m src.host --name host1 --probe host2 --count 3 --timeout 2 --config config/topology_upper.json
```

Each process prints its own events as JSON lines. Stop the routers and host2 with Ctrl-C in each terminal. The probing host exits on its own and prints `N/M probes answered`. Its exit code is 0 only if every probe was answered. Add the same `--run-id` to every command to group the logs in one folder. For the lower path, use `config/topology_lower.json`. Host2 can also probe host1 by swapping the roles.

## Demo Scenarios

Each scenario uses the [live view](#live-view-two-terminals): a terminal 1 command that starts the network, and terminal 2 commands that send traffic. Scenarios with the same run ID share one network. For the others, press Ctrl-C in terminal 1 and start it again with the new command. Every output below was produced by running these commands. Times and IDs will differ.

### Probe and echo

**Terminal 1:**

```bash
python -m scripts.demo_net --run-id demo --replace
```

**Terminal 2:**

```bash
python -m src.host --name host1 --probe host2 --count 3 --run-id demo
```

The output is in [Quick Start](#5-watch-the-network-run). host2 answers each `PROBE` with an `ECHO` carrying the same message ID and payload. host1 accepts an echo only if the ID, the sender and the payload all match, then logs `echo_received` with `rtt_ms`. An echo whose payload differs is logged as `echo_payload_mismatch` and ignored. By default probes carry `probe-0001`, `probe-0002`, and so on (always 10 bytes). `--payload` sets a fixed payload instead.

### Unknown destination

With the `demo` network still running, probe routerB. It's a device in the config, but no router has a forwarding entry for it:

**Terminal 2:**

```bash
python -m src.host --name host1 --probe routerB --timeout 1 --run-id demo
```

```text
14:44:47.868  routerA  DROPS PROBE host1→routerB: unknown_destination  [host1-bf3f30cc-1]
14:44:48.869  host1    TIMEOUT: no echo within 1.0 s  [host1-bf3f30cc-1]
```

A name that isn't in the config at all, such as `hostZ`, is rejected by host1 before anything is sent.

### Invalid packet

With the `demo` network still running, send bytes that aren't a packet to routerA's port (5101):

**Terminal 2:**

```bash
python -c "import socket; socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b'not a packet', ('127.0.0.1', 5101))"
```

```text
14:44:48.914  routerA  DROPS a malformed packet: Invalid JSON packet
```

routerA keeps running.

### Loss and delay on one link

Restart terminal 1 with 80 ms delay each way and 30% loss on routerB–routerD:

**Terminal 1:**

```bash
python -m scripts.demo_net --run-id lossy --replace --link routerB-routerD --delay-ms 80 --loss 0.3 --seed 4
```

**Terminal 2:**

```bash
python -m src.host --name host1 --probe host2 --count 4 --interval 0.3 --timeout 1 --run-id lossy
```

```text
14:44:50.731  host1    gets the ECHO back: round trip 165.8 ms  [host1-9956f28f-1]
14:44:51.036  routerB  DROPS PROBE on the link to routerD (random loss 30%)  [host1-9956f28f-2]
14:44:52.037  host1    TIMEOUT: no echo within 1.0 s  [host1-9956f28f-2]
14:44:52.505  host1    gets the ECHO back: round trip 166.6 ms  [host1-9956f28f-3]
14:44:52.977  host1    gets the ECHO back: round trip 166.4 ms  [host1-9956f28f-4]
```

The round trip is about twice the delay plus a few milliseconds. The 30% loss rate makes a drop likely within four probes. With seed 4 and these commands, probe 2 is the one lost every time.

### Forced drop of one packet

Restart terminal 1 with a rule that makes routerB drop the second probe, once:

**Terminal 1:**

```bash
python -m scripts.demo_net --run-id forced --replace --drop-rule "routerB:type=PROBE,skip=1"
```

**Terminal 2:**

```bash
python -m src.host --name host1 --probe host2 --count 3 --interval 0.3 --timeout 1 --run-id forced
```

```text
14:44:54.293  host1    gets the ECHO back: round trip 1.8 ms  [host1-cd63d51e-1]
14:44:54.599  routerB  DROPS PROBE on the link to routerD (forced rule type=PROBE,skip=1,count=1)  [host1-cd63d51e-2]
14:44:55.600  host1    TIMEOUT: no echo within 1.0 s  [host1-cd63d51e-2]
14:44:55.905  host1    gets the ECHO back: round trip 2.2 ms  [host1-cd63d51e-3]
```

Terminal 2 prints `2/3 probes answered`.

### Forwarding loop

Restart terminal 1 with the loop config, then send a probe to routerD with a small hop limit:

**Terminal 1:**

```bash
python -m scripts.demo_net --run-id loop --replace --config config/topology_loop.json
```

**Terminal 2:**

```bash
python -m src.host --name host1 --probe routerD --hop-limit 6 --timeout 1 --run-id loop
```

```text
14:43:29.175  host1    sends PROBE host1→routerD to routerA (hop limit 6)
14:43:29.175  routerA  forwards PROBE host1→routerD to routerB, hop limit now 5
14:43:29.175  routerB  forwards PROBE host1→routerD to routerA, hop limit now 4
14:43:29.176  routerA  forwards PROBE host1→routerD to routerB, hop limit now 3
14:43:29.176  routerB  forwards PROBE host1→routerD to routerA, hop limit now 2
14:43:29.176  routerA  forwards PROBE host1→routerD to routerB, hop limit now 1
14:43:29.176  routerB  DROPS PROBE host1→routerD: hop_limit_exhausted
14:43:30.176  host1    TIMEOUT: no echo within 1.0 s
```

In this config, routerA and routerB send packets for routerD to each other, so the probe bounces until its hop limit runs out. The validator rejects loops on the host1↔host2 routes in `paths`, so the loop uses routerD as the destination instead. Traffic between the hosts is routed normally in this config.

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

All links in the committed configs have 0 loss and 0 ms delay. The live view and the experiments set loss and delay on one link at run time.

### Forced drop rules

A rule drops chosen packets a fixed number of times, so a test can lose one specific packet instead of relying on chance:

```bash
python -m src.router --name routerB --drop-rule "type=PROBE,skip=1"
```

A rule matches on any of `message_id`, `type`, `src`, `dst` and `next_hop` (the neighbor the packet is about to be sent to). `skip=N` lets the first N matching packets through, and `count=N` drops the next N (default 1). After that, matching packets go through again, so a retransmission with the same ID is delivered. `--drop-rule` can be given more than once. The live view and the launcher take the same rule prefixed with a router name, for example `routerB:type=PROBE,skip=1`.

## Logging

Every host and router writes one JSON object per event, both to the terminal and to `logs/<run-id>/<device>.jsonl`. For example, this router writes `logs/trial-3/routerA.jsonl`:

```bash
python -m src.router --name routerA --run-id trial-3
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

It prints one line, such as `demo-1: 19/20 probes answered (5.907 s) -> results/manual/demo-1`. Each run gets a folder holding the items below. Single runs go under `results/manual/`, which git ignores, unless `--out` names another folder.

- `config.json`: the exact topology used, with the link change applied
- `run.json`: seed, router seeds, parameters, start and end times, the sender's result, and the commit the code ran from
- one `<device>.jsonl` log per process

The launcher refuses to overwrite an existing run folder. The run seed sets each router's seed as `seed × 10 + i` (routerA = +0 to routerD = +3), so routers don't share a random sequence. `--drop-rule routerB:type=PROBE,skip=1` adds a forced drop rule to one router.

### Baseline

The committed results are in `results/baseline/`. To repeat the experiment without overwriting them (about 1.5 minutes):

```bash
python -m scripts.run_baseline --out results/manual/baseline-check
python -m scripts.plot_results results/manual/baseline-check
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
