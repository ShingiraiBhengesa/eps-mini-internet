
import json
import sys
import time


def create_logger(run_id, stream=None):
    output = sys.stdout if stream is None else stream

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

        print(
            json.dumps(record, default=str, separators=(",", ":")),
            file=output,
            flush=True
        )

    return log
