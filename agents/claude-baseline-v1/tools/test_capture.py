"""Verify event evidence survives selection, compression and extraction."""
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from core.capture import Capture, encode
from extract_capture import extract


class CaptureTests(unittest.TestCase):
    def recover(self, messages):
        log = io.StringIO()
        capture = Capture(log)
        for message in messages:
            capture.record(json.dumps(message) + "\n")
        capture.flush()
        output = io.StringIO()
        extract(io.StringIO(log.getvalue()), output)
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def test_public_rows_reference(self):
        rows = [["T1", 1.5, 2.5]]
        record = self.recover([{"message_type": "initialize", "payload": {
            "targets": {"columns": ["id", "ra", "dec"], "rows": rows}, "scoring": {"x": 1}}}])[0]
        self.assertNotIn("rows", record["message"]["payload"]["targets"])
        self.assertEqual(record["omitted"]["payload.targets.rows"]["sha256"], hashlib.sha256(encode(rows)).hexdigest())
        self.assertEqual(record["message"]["payload"]["scoring"], {"x": 1})

    def test_preserves_evidence_and_unknown_fields(self):
        bulletin = {"record_type": "bulletin", "issued_at_utc": "t", "notices": []}
        payload = {"now_utc": "t", "new_messages": [bulletin, {"record_type": "state_resync", "invalidated_window": [1, 2]}],
                   "last_result": {"action": "observe", "hits": [{"target_id": "T1", "score": .5}]},
                   "latest_bulletin": bulletin, "latest_forecast": None,
                   "active_requests": [], "wallclock": {"remaining_seconds": 50},
                   "survey_end_utc": "end", "future_field": {"unknown": True}}
        out = self.recover([{"message_type": "decision_request", "payload": payload}])[0]
        retained = out["message"]["payload"]
        for key in ("now_utc", "new_messages", "last_result", "future_field"):
            self.assertEqual(retained[key], payload[key])
        for key in ("latest_bulletin", "active_requests", "wallclock", "survey_end_utc"):
            self.assertNotIn(key, retained)
            self.assertIn(key, out["omitted_state_fields"])

    def test_unannounced_latest_snapshot_kept_once(self):
        message = {"message_type": "decision_request", "payload": {"new_messages": [], "latest_forecast": {"record_type": "forecast", "unexpected": 1}}}
        records = self.recover([message, message])
        self.assertIn("latest_forecast", records[0]["message"]["payload"])
        self.assertNotIn("latest_forecast", records[1]["message"]["payload"])

    def test_detects_missing_batch(self):
        log = io.StringIO()
        capture = Capture(log)
        for _ in range(65):
            capture.record("{}\n")
        capture.flush()
        with self.assertRaises(ValueError):
            extract(io.StringIO(log.getvalue().splitlines(keepends=True)[1]), io.StringIO())


if __name__ == "__main__":
    unittest.main()
