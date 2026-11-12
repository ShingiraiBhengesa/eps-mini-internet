
import argparse
import json
import re
import sys
import time
from pathlib import Path

# Run IDs become folder names under the log directory, so keep them simple.
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
DEFAULT_LOG_DIR = "logs"


def run_id_arg(value):
    """argparse type for --run-id: letters, digits, '.', '_' and '-' only."""
    if not RUN_ID_PATTERN.match(value):
        raise argparse.ArgumentTypeError(
            "run ID must be 1-64 characters: letters, digits, '.', '_' or '-', "
            "starting with a letter or digit"
        )
    return value


def log_path(log_dir, run_id, device):
    """Where one device's log for one run is written."""
    return Path(log_dir) / run_id / f"{device}.jsonl"


def create_logger(run_id, stream=None, path=None):
    """Return log(device, event, packet=None, **details).

    Every call writes one JSON line to `stream` (stdout by default). When
    `path` is given the line is also appended to that file; the file and
    its folders are created on the first write, so a program that fails
    before logging anything leaves no empty files behind.
    """
    output = sys.stdout if stream is None else stream
    state = {"file": None}

    def log(device, event, packet=None, **details):
        record = {
            "run_id": run_id,
            "device": device,
            "timestamp": time.time(),
            "event": event,
        }

        if packet is not None:
            for key in ("message_id", "type", "src", "dst", "hop_limit"):
                record[key] = packet[key]

        record.update(details)
        line = json.dumps(record, default=str, separators=(",", ":"))

        print(line, file=output, flush=True)

        if path is not None:
            if state["file"] is None:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                state["file"] = open(path, "a", encoding="utf-8")
            state["file"].write(line + "\n")
            state["file"].flush()

    def close():
        if state["file"] is not None:
            state["file"].close()
            state["file"] = None

    log.close = close
    return log
