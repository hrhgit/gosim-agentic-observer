"""Event evidence capture, omitting public target rows and known repeated state."""
import base64
import hashlib
import json
import zlib

PREFIX = "SURVEY26_CAPTURE_V2 "


def encode(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")


class Capture:
    def __init__(self, stream):
        self.stream = stream
        self.sequence = 0
        self.pending = []
        self.latest = {}

    def record(self, raw, annotations=None):
        self.sequence += 1
        entry = {"received_sequence": self.sequence}
        if annotations is not None:
            entry["annotations"] = annotations
        try:
            message = json.loads(raw)
        except ValueError:
            entry["raw"] = raw
            message = None
        else:
            entry["message"] = message
            if isinstance(message, dict) and message.get("message_type") == "initialize":
                targets = message.get("payload", {}).get("targets", {})
                if "rows" in targets:
                    rows = targets.pop("rows")
                    entry["omitted"] = {"payload.targets.rows": {
                        "source": "official-public-targets",
                        "row_count": len(rows),
                        "sha256": hashlib.sha256(encode(rows)).hexdigest(),
                        "hash_encoding": "json-ascii-compact-ordered-rows-v1",
                    }}
            elif isinstance(message, dict) and message.get("message_type") == "decision_request":
                payload = message.get("payload", {})
                omitted = []
                # Requests themselves arrive in new_messages. Progress can be
                # analysed from observation feedback and state_resync records.
                for key in ("wallclock", "active_requests", "survey_end_utc"):
                    if key in payload:
                        payload.pop(key)
                        omitted.append(key)
                for key in ("latest_bulletin", "latest_forecast"):
                    if key not in payload:
                        continue
                    value = payload[key]
                    repeated = value == self.latest.get(key) or value in (payload.get("new_messages") or [])
                    self.latest[key] = value
                    if repeated:
                        payload.pop(key)
                        omitted.append(key)
                if omitted:
                    entry["omitted_state_fields"] = omitted
        self.pending.append(encode(entry) + b"\n")
        kind = message.get("message_type") if isinstance(message, dict) else None
        if len(self.pending) >= 64 or kind in ("initialize", "finish"):
            self.flush()

    def flush(self):
        if not self.pending:
            return
        raw = b"".join(self.pending)
        packet = {
            "first": self.sequence - len(self.pending) + 1,
            "last": self.sequence,
            "count": len(self.pending),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "data": base64.b64encode(zlib.compress(raw, 6)).decode("ascii"),
        }
        self.stream.write(PREFIX + encode(packet).decode("ascii") + "\n")
        self.stream.flush()
        self.pending.clear()
