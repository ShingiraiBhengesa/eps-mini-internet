"""Track B evidence and comparison runs.

    python -m scripts.run_trackb evidence
    python -m scripts.run_trackb compare

evidence  The four fixed stop-and-wait cases, each checked automatically:
          no loss, one DATA dropped, the final ACK dropped, and an
          unreachable destination. Results: results/trackb/evidence/.

compare   Best-effort vs stop-and-wait at 0% and 5% loss on routerB-routerD,
          3 seeded runs each (12 runs). Results: results/trackb/compare/.

Optional enhancement, Go-Back-N:

    python -m scripts.run_trackb evidence --mode gobackn
    python -m scripts.run_trackb window

evidence --mode gobackn   Five fixed cases with a window of 4, checked
          automatically. Results: results/trackb/window-evidence/.
window    Go-Back-N with windows 1, 2, 4 and 8 at 0% and 5% loss, 3 seeded
          runs each (24 runs), with the same settings as `compare`.
          Results: results/trackb/window/.

Both use the upper path (config/topology_upper.json), so DATA and ACKs
both cross routerB-routerD, with a 20 ms one-way delay on that link.
"""

import argparse
import csv
import sys
from pathlib import Path

from scripts import launch, metrics

CONFIG = "config/topology_upper.json"
LINK = "routerB-routerD"
DELAY_MS = 20

# Evidence: small transfers with forced drops at known points.
EVIDENCE = dict(count=10, rate=20.0, timeout=0.3, retries=5, linger=2.0)

# Comparison: the same messages, path, delay and maximum rate for both modes.
COMPARE = dict(count=50, rate=50.0, timeout=0.3, retries=5, linger=2.0)
OBSERVE_S = 3.0   # best-effort receiver counts unique messages for this long

# Enhancement evidence: a higher rate (a send every 10 ms against a 44 ms
# round trip) so a window of 4 actually fills.
WINDOW_EVIDENCE = dict(count=10, rate=100.0, timeout=0.3, retries=5, linger=2.0)
EVIDENCE_WINDOW = 4
WINDOWS = [1, 2, 4, 8]


def evidence_cases():
    n = EVIDENCE["count"]
    return [
        {
            "case": "no-loss",
            "what": "0% loss: every message delivered once, in order",
            "drop_rules": [],
            "start_receiver": True,
            "expect": dict(success=True, delivered=n, full_sequence=True, in_order=True,
                           duplicates=0, retransmissions=0),
        },
        {
            "case": "data-drop",
            "what": "DATA 5 dropped once at routerB: resent and recovered",
            "drop_rules": [("routerB", "type=DATA,message_id=sw-ev-data-drop-5")],
            "start_receiver": True,
            "expect": dict(success=True, delivered=n, full_sequence=True, in_order=True,
                           duplicates=0, retransmissions=1, data_drops=1),
        },
        {
            "case": "final-ack-drop",
            "what": f"final ACK (message {n}) dropped once at routerD: sender resends, "
                    "lingering receiver ACKs again",
            "drop_rules": [("routerD", f"type=ACK,message_id=sw-ev-final-ack-drop-{n}")],
            "start_receiver": True,
            "expect": dict(success=True, delivered=n, full_sequence=True, in_order=True,
                           duplicates=1, retransmissions=1, ack_drops=1,
                           receiver_stop="linger_over"),
        },
        {
            "case": "unreachable",
            "what": "host2 not running: sender gives up after its retry limit",
            "drop_rules": [],
            "start_receiver": False,
            "expect": dict(success=False, delivered=0, failed_at=1,
                           retransmissions=EVIDENCE["retries"]),
        },
    ]


def window_evidence_cases(window):
    """Go-Back-N's fixed cases; transfer IDs are wev-<case>."""
    n = WINDOW_EVIDENCE["count"]

    def goes_back(m, run_dir):
        sender_log = metrics.read_jsonl(run_dir / "host1.jsonl")
        resent = [r["seq"] for r in sender_log if r.get("event") == "retransmit"]
        problems = []
        if not resent or resent[0] != 5:
            problems.append(f"first resend should be message 5, got {resent[:1]}")
        if not 1 <= m["retransmissions"] <= window:
            problems.append(f"resent {m['retransmissions']}, expected 1 to {window} (what was in flight)")
        if m["out_of_order_discards"] < 1:
            problems.append("the receiver should discard what arrived after the gap")
        return problems

    return [
        {
            "case": "no-loss",
            "what": f"0% loss: every message once, in order, with the window of {window} filled",
            "drop_rules": [],
            "start_receiver": True,
            "expect": dict(success=True, delivered=n, full_sequence=True, in_order=True,
                           duplicates=0, retransmissions=0, max_in_flight=window),
        },
        {
            "case": "data-drop",
            "what": "DATA 5 dropped once at routerB: the receiver discards what follows the "
                    "gap, and the sender goes back to 5 and resends what was in flight",
            "drop_rules": [("routerB", "type=DATA,message_id=gbn-wev-data-drop-5")],
            "start_receiver": True,
            "expect": dict(success=True, delivered=n, full_sequence=True, in_order=True,
                           duplicates=0, data_drops=1),
            "verify": goes_back,
        },
        {
            "case": "lost-ack",
            "what": "ACK 3 dropped once at routerD: the next cumulative ACK covers it, "
                    "so nothing is resent",
            "drop_rules": [("routerD", "type=ACK,message_id=gbn-wev-lost-ack-3")],
            "start_receiver": True,
            "expect": dict(success=True, delivered=n, full_sequence=True, in_order=True,
                           duplicates=0, retransmissions=0, ack_drops=1),
        },
        {
            "case": "final-ack-drop",
            "what": f"final ACK (message {n}) dropped once at routerD: the sender resends "
                    "it, the lingering receiver ACKs again",
            "drop_rules": [("routerD", f"type=ACK,message_id=gbn-wev-final-ack-drop-{n}")],
            "start_receiver": True,
            "expect": dict(success=True, delivered=n, full_sequence=True, in_order=True,
                           duplicates=1, retransmissions=1, ack_drops=1,
                           receiver_stop="linger_over"),
        },
        {
            "case": "unreachable",
            "what": f"host2 not running: each timeout resends the window of {window}; "
                    "the sender gives up after its retry limit",
            "drop_rules": [],
            "start_receiver": False,
            "expect": dict(success=False, delivered=0, failed_at=1,
                           retransmissions=WINDOW_EVIDENCE["retries"] * window),
        },
    ]


OBSERVED_KEYS = {
    "success": "sender_success",
    "duplicates": "duplicates_at_receiver",
}


def check(case, m, run_dir=None):
    """Compare a run's metrics with the case's expectations."""
    problems = list(case["verify"](m, run_dir)) if "verify" in case else []
    for key, wanted in case["expect"].items():
        got = m[OBSERVED_KEYS.get(key, key)]
        if got != wanted:
            problems.append(f"{key}: expected {wanted}, got {got}")

    if case["case"] == "unreachable":
        bound = (EVIDENCE["retries"] + 1) * EVIDENCE["timeout"]   # same in WINDOW_EVIDENCE
        elapsed = m["sender_elapsed_s"] or 0
        if not bound <= elapsed < bound + 1.0:
            problems.append(f"gave up after {elapsed} s, expected about {bound:.1f} s")
    return problems


def run_evidence(out, config=CONFIG, mode="stopwait", window=EVIDENCE_WINDOW):
    rows = []
    gbn = mode == "gobackn"
    cases = window_evidence_cases(window) if gbn else evidence_cases()
    settings = WINDOW_EVIDENCE if gbn else EVIDENCE
    for case in cases:
        run_id = f"{'window-' if gbn else ''}evidence-{case['case']}"
        if (out / run_id).exists():
            print(f"error: {out / run_id} already exists; delete {out} to rerun",
                  file=sys.stderr)
            return 2

        run_dir, meta = launch.run_transfer(
            config, run_id, seed=0, out_dir=out, mode=mode,
            window=window if gbn else None,
            transfer_id=f"{'wev' if gbn else 'ev'}-{case['case']}",
            start_receiver=case["start_receiver"],
            link=LINK, loss=0.0, delay_ms=DELAY_MS,
            drop_rules=case["drop_rules"], condition=case["case"], **settings,
        )
        m = metrics.run_metrics(run_dir)
        problems = check(case, m, run_dir)
        rows.append({
            "case": case["case"],
            "what": case["what"],
            "result": "PASS" if not problems else "FAIL: " + "; ".join(problems),
            "delivered": f"{m['delivered']}/{m['messages']}",
            "in_order": m["in_order"],
            "duplicates_at_receiver": m["duplicates_at_receiver"],
            "retransmissions": m["retransmissions"],
            "max_in_flight": m["max_in_flight"],
            "out_of_order_discards": m["out_of_order_discards"],
            "sender_success": m["sender_success"],
            "sender_elapsed_s": m["sender_elapsed_s"],
            "receiver_stop": m["receiver_stop"],
        })
        print(f"{case['case']:<15} {rows[-1]['result']:<6} {meta['sender_summary']}")
        if problems:
            print("                " + "; ".join(problems))

    with open(out / "summary.csv", "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nWrote {out / 'summary.csv'}")
    return 0 if all(r["result"] == "PASS" for r in rows) else 1


def compare_plan(losses, modes, runs, first_seed):
    seed = first_seed
    for loss in losses:
        for mode in modes:
            for _ in range(runs):
                yield loss, mode, seed
                seed += 1


MODE_LABEL = {"besteffort": "best-effort", "stopwait": "stop-and-wait"}


def run_compare(out, losses, runs, first_seed, config=CONFIG):
    for loss, mode, seed in compare_plan(losses, ["besteffort", "stopwait"], runs, first_seed):
        short = "be" if mode == "besteffort" else "sw"
        run_id = f"trackb-{short}-l{round(loss * 100)}-s{seed}"
        if (out / run_id).exists():
            print(f"error: {out / run_id} already exists; delete {out} to rerun",
                  file=sys.stderr)
            return 2

        _, meta = launch.run_transfer(
            config, run_id, seed, out, mode=mode,
            observe=OBSERVE_S if mode == "besteffort" else None,
            link=LINK, loss=loss, delay_ms=DELAY_MS,
            condition=f"{MODE_LABEL[mode]}, loss {loss:.0%}", **COMPARE,
        )
        print(f"{run_id}: {meta['sender_summary']}")

    _, conditions = metrics.summarize(out)
    print()
    print(metrics.format_transfer_table(conditions))
    return 0


def run_window(out, losses, windows, runs, first_seed, config=CONFIG):
    """Go-Back-N at each window size, with the comparison's exact settings."""
    seed = first_seed
    for loss in losses:
        for window in windows:
            for _ in range(runs):
                run_id = f"trackb-gbn{window}-l{round(loss * 100)}-s{seed}"
                if (out / run_id).exists():
                    print(f"error: {out / run_id} already exists; delete {out} to rerun",
                          file=sys.stderr)
                    return 2
                _, meta = launch.run_transfer(
                    config, run_id, seed, out, mode="gobackn", window=window,
                    link=LINK, loss=loss, delay_ms=DELAY_MS,
                    condition=f"go-back-n window {window}, loss {loss:.0%}", **COMPARE,
                )
                print(f"{run_id}: {meta['sender_summary']}")
                seed += 1

    _, conditions = metrics.summarize(out)
    print()
    print(metrics.format_transfer_table(conditions))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Track B evidence and comparison runs.")
    commands = parser.add_subparsers(dest="command", required=True)

    evidence = commands.add_parser("evidence", help="the fixed cases, checked automatically")
    evidence.add_argument("--mode", choices=["stopwait", "gobackn"], default="stopwait")
    evidence.add_argument("--window", type=int, default=EVIDENCE_WINDOW,
                          help="Go-Back-N window (default %(default)s)")
    evidence.add_argument("--out", help="default results/trackb/evidence, or "
                                        "results/trackb/window-evidence for gobackn")
    evidence.add_argument("--config", default=CONFIG)

    compare = commands.add_parser("compare", help="best-effort vs stop-and-wait, 12 runs")
    compare.add_argument("--out", default="results/trackb/compare")
    compare.add_argument("--losses", type=float, nargs="+", default=[0.0, 0.05])
    compare.add_argument("--runs", type=int, default=3, help="runs per condition")
    compare.add_argument("--first-seed", type=int, default=101)
    compare.add_argument("--config", default=CONFIG)

    window = commands.add_parser("window", help="Go-Back-N at windows 1, 2, 4, 8 (24 runs)")
    window.add_argument("--out", default="results/trackb/window")
    window.add_argument("--losses", type=float, nargs="+", default=[0.0, 0.05])
    window.add_argument("--windows", type=int, nargs="+", default=WINDOWS)
    window.add_argument("--runs", type=int, default=3, help="runs per condition")
    window.add_argument("--first-seed", type=int, default=201)
    window.add_argument("--config", default=CONFIG)

    args = parser.parse_args(argv)
    if args.command == "evidence" and args.out is None:
        args.out = ("results/trackb/window-evidence" if args.mode == "gobackn"
                    else "results/trackb/evidence")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    try:
        if args.command == "evidence":
            return run_evidence(out, args.config, args.mode, args.window)
        if args.command == "window":
            return run_window(out, args.losses, args.windows, args.runs,
                              args.first_seed, args.config)
        return run_compare(out, args.losses, args.runs, args.first_seed, args.config)
    except (launch.LaunchError, launch.ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("If a port is in use, stop old processes with: "
              "pkill -f 'src.(router|host|reliable)'", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
