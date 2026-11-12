# Wireshark Evidence

> **To do before submission:** take the two captures below, save them in this folder, and fill in the **Frame** columns in the two path tables. Then delete this note.

Everything runs on localhost, so every packet between devices crosses the loopback interface (`lo0` on a Mac) and can be captured there. The two captures:

| File | Shows |
|---|---|
| `core.pcapng` | One probe from host1 to host2 and its echo back, hop by hop |
| `trackb-retransmission.pcapng` | A Track B transfer where DATA 3 is dropped once and resent |

The matching device logs are copied next to each capture (`core-logs/`, `trackb-logs/`), so every frame can be matched to a log line.

## How to capture

### Install Wireshark (once)

Download Wireshark for macOS from [wireshark.org](https://www.wireshark.org/download.html) and install it. When the installer offers **ChmodBPF**, accept it: it lets Wireshark capture without an administrator password each time. (Without Wireshark, see [Using tcpdump instead](#using-tcpdump-instead).)

In every terminal, first run:

```bash
cd ~/eps-mini-internet
source .venv/bin/activate
```

### Capture A: one probe and its echo

1. **Terminal 1:** start the network.

   ```bash
   python -m scripts.demo_net --run-id wireshark-core --replace
   ```

2. **Wireshark:** select the **Loopback: lo0** interface. In the capture filter box, enter the filter below, then start capturing.

   ```text
   udp and (port 5001 or port 5002 or portrange 5101-5104)
   ```

3. **Terminal 2:** send one probe.

   ```bash
   python -m src.host --name host1 --probe host2 --count 1 --run-id wireshark-core
   ```

4. **Wireshark:** stop the capture. You should see 8 packets. Save with File, Save As, as `results/wireshark/core.pcapng`.
5. **Terminal 1:** press Ctrl-C, then keep the logs with the capture:

   ```bash
   cp -R logs/wireshark-core results/wireshark/core-logs
   ```

### Capture B: a Track B retransmission

1. **Terminal 1:** start the network with a rule that makes routerB drop DATA 3 once.

   ```bash
   python -m scripts.demo_net --run-id wireshark-trackb --replace --drop-rule "routerB:type=DATA,message_id=sw-ws-3"
   ```

2. **Wireshark:** start a new capture on **lo0** with the same capture filter.
3. **Terminal 2:** send 5 messages with stop-and-wait. `--transfer-id ws` makes the message IDs `sw-ws-1` to `sw-ws-5`.

   ```bash
   python -m src.reliable send --name host1 --to host2 --count 5 --transfer-id ws --run-id wireshark-trackb
   ```

4. **Wireshark:** stop the capture, about a second later. You should see 42 packets: 8 for each of messages 1, 2, 4 and 5, and 10 for message 3. Save it as `results/wireshark/trackb-retransmission.pcapng`.
5. **Terminal 1:** press Ctrl-C, then:

   ```bash
   cp -R logs/wireshark-trackb results/wireshark/trackb-logs
   ```

### Reading a capture

- **Show only project traffic:** display filter `udp.port in {5001 5002 5101 5102 5103 5104}`.
- **Read the payload:** select a packet. The bytes pane shows the JSON as text. For a parsed view, use Analyze, Decode As, set **UDP port** 5001 (and the other ports) to **JSON**.
- **Match frames to log lines:** set View, Time Display Format, **Seconds Since Epoch**. Wireshark's time column then uses the same clock as the `timestamp` field in the logs. Each device logs `sent` right after it sends, so a frame and its `sent` line are within a millisecond.
- **Hop by hop in the logs:** `python -m scripts.trace results/wireshark/trackb-logs 3` prints message 3's journey with timings, one path per attempt.

### Using tcpdump instead

macOS includes `tcpdump`, which needs an administrator password to capture. Start it in a third terminal before step 3 of each capture, and stop it with Ctrl-C after:

```bash
sudo tcpdump -i lo0 -P -w results/wireshark/core.pcapng "udp and (port 5001 or port 5002 or portrange 5101-5104)"
```

`-P` writes the pcapng format; Wireshark opens the file afterwards on any computer. Reading a capture needs no password: `tcpdump -n -tt -A -r results/wireshark/core.pcapng`.

## Write-up

### Which UDP port belongs to which device

Every device is one process with one UDP socket on 127.0.0.1, and it sends from the same port it listens on. So a packet's UDP source port says which device sent it, and its destination port says which device receives it next.

| Device | Virtual address | UDP port |
|---|---|---|
| host1 | `host1` | 5001 |
| host2 | `host2` | 5002 |
| routerA | `routerA` | 5101 |
| routerB | `routerB` | 5102 |
| routerC | `routerC` | 5103 |
| routerD | `routerD` | 5104 |

The UDP ports change at every hop, while the JSON `src` and `dst` stay the same from end to end. The ports are the link layer of our virtual network; `src` and `dst` are its network-layer addresses.

### What the payload fields mean

Each UDP payload is one JSON object, UTF-8, at most 1,200 bytes. The probe in capture A looks like this (114 bytes; its echo is 113, because `ECHO` is one letter shorter than `PROBE`):

```json
{"src":"host1","dst":"host2","hop_limit":16,"type":"PROBE","message_id":"host1-3622e93a-1","payload":"probe-0001"}
```

| Field | Meaning |
|---|---|
| `src` | Virtual address of the host that created the packet. Routers don't change it |
| `dst` | Virtual address the packet is for. Each router looks it up in its forwarding table |
| `hop_limit` | Hops left. Each router lowers it by one before forwarding, and drops a packet that arrives with 1 or less |
| `type` | `PROBE` and `ECHO` for reachability; `DATA` and `ACK` for Track B |
| `message_id` | Matches a reply to its request. An ECHO reuses its probe's ID; an ACK reuses its DATA's ID. Track B IDs are `<mode>-<transfer>-<n>` |
| `payload` | The content. A probe's payload comes back unchanged in the echo; Track B DATA carries `data-0001` and so on; an ACK's payload is empty |

In capture B, a DATA message is 103 bytes and an ACK 93:

```json
{"src":"host1","dst":"host2","hop_limit":16,"type":"DATA","message_id":"sw-ws-3","payload":"data-0003"}
{"src":"host2","dst":"host1","hop_limit":16,"type":"ACK","message_id":"sw-ws-3","payload":""}
```

### Capture A: request and response path

The probe goes out through routerB and the echo comes back the same way, because routerA and routerD both send host-bound traffic via routerB. Each row is one UDP datagram, and one log line records each send.

| # | UDP ports | Hop | JSON (`type`, `src`→`dst`, `hop_limit`) | Log line (device: event) | Frame |
|---|---|---|---|---|---|
| 1 | 5001 → 5101 | host1 → routerA | PROBE, host1→host2, 16 | host1: `sent` | |
| 2 | 5101 → 5102 | routerA → routerB | PROBE, host1→host2, 15 | routerA: `forward`, `sent` | |
| 3 | 5102 → 5104 | routerB → routerD | PROBE, host1→host2, 14 | routerB: `forward`, `sent` | |
| 4 | 5104 → 5002 | routerD → host2 | PROBE, host1→host2, 13 | routerD: `forward`, `sent` | |
| 5 | 5002 → 5104 | host2 → routerD | ECHO, host2→host1, 16 | host2: `sent` | |
| 6 | 5104 → 5102 | routerD → routerB | ECHO, host2→host1, 15 | routerD: `forward`, `sent` | |
| 7 | 5102 → 5101 | routerB → routerA | ECHO, host2→host1, 14 | routerB: `forward`, `sent` | |
| 8 | 5101 → 5001 | routerA → host1 | ECHO, host2→host1, 13 | routerA: `forward`, `sent`; then host1: `echo_received` | |

All 8 carry the same `message_id`. The time between frames 1 and 8 matches the `rtt_ms` in host1's `echo_received` line.

### Capture B: a retransmission

Messages 1, 2, 4 and 5 each look like capture A: 4 DATA datagrams out and 4 ACK datagrams back. Message 3 is different:

| # | UDP ports | Hop | JSON (`type`, `message_id`, `hop_limit`) | Log line (device: event) | Frame |
|---|---|---|---|---|---|
| 1 | 5001 → 5101 | host1 → routerA | DATA, `sw-ws-3`, 16 | host1: `sent` | |
| 2 | 5101 → 5102 | routerA → routerB | DATA, `sw-ws-3`, 15 | routerA: `forward`, `sent` | |
| | *none* | routerB → routerD | | routerB: `drop_forced` | not on the wire |
| | *about 0.5 s with no message 3 traffic* | | | host1: `ack_timeout`, `retransmit` | |
| 3 | 5001 → 5101 | host1 → routerA | DATA, `sw-ws-3`, 16 | host1: `sent` (attempt 2) | |
| 4 | 5101 → 5102 | routerA → routerB | DATA, `sw-ws-3`, 15 | routerA: `forward`, `sent` | |
| 5 | 5102 → 5104 | routerB → routerD | DATA, `sw-ws-3`, 14 | routerB: `forward`, `sent` | |
| 6 | 5104 → 5002 | routerD → host2 | DATA, `sw-ws-3`, 13 | routerD: `sent`; host2: `delivered` | |
| 7 | 5002 → 5104 | host2 → routerD | ACK, `sw-ws-3`, 16 | host2: `sent` | |
| 8 | 5104 → 5102 | routerD → routerB | ACK, `sw-ws-3`, 15 | routerD: `forward`, `sent` | |
| 9 | 5102 → 5101 | routerB → routerA | ACK, `sw-ws-3`, 14 | routerB: `forward`, `sent` | |
| 10 | 5101 → 5001 | routerA → host1 | ACK, `sw-ws-3`, 13 | routerA: `sent`; host1: `ack_received` | |

The capture shows the retransmission directly: host1 sends DATA `sw-ws-3` from port 5001 twice, about 0.5 s apart (the ACK timeout). The first copy stops after routerA → routerB, and only the second reaches host2 and gets an ACK.

### What a capture can't show

A router drops a packet *before* sending it, so a dropped packet never reaches the wire. That covers forced drops, random loss and failed forwarding checks such as `hop_limit_exhausted`. The capture shows a drop only as a gap: in capture B, no datagram from 5102 to 5104 follows frame 2. The router's log line (`drop_forced`, `drop_injected` or `drop` with its reason) is the evidence for the drop itself. Loss and delay are applied by the routers in our code, not by the operating system, so they also appear only as timing and gaps in a capture.
