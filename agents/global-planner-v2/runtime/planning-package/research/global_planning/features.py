"""Versioned public-only numeric projection; IDs, text, teacher and outcomes excluded."""
from __future__ import annotations

from datetime import datetime
import math
import numpy as np

VERSION = "public-planning-features-v1"
CONDITION_FIELDS = ("mean_quality", "q05", "q50", "q95", "usable_fraction", "u05", "u50", "u95",
                    "severe_block_probability", "p_dark", "p_bright", "p_backup")
BASE_FIELDS = ("weather", "throughput", "efficiency", "has_measurement", "suspected_fault", "suspected_closure", "quake_notice")
CANDIDATE_FIELDS = ("duration_hours", "nv", "quality", "urgency", "expected_science", "future", "potential", "deep", "required_left", "assigned_fraction")


def num(value):
    if value is None:
        return 0.
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Nonfinite public feature")
    return result


def stats(values):
    return [float(f(values)) for f in (np.mean, np.std, np.min, np.max)] if len(values) else [0.] * 4


def position(ra, dec):
    ra, dec = math.radians(ra), math.radians(dec)
    return [math.sin(ra), math.cos(ra), math.sin(dec), math.cos(dec)]


def encode(frame):
    if frame["schema"] != "global-planning-frame-v1":
        raise ValueError("Unsupported planning frame")
    g = frame["global"]
    now = datetime.fromisoformat(frame["now_utc"].replace("Z", "+00:00")).timestamp()
    x = [num(g[k]) for k in ("night_fraction_left", "total_time_fraction_left", "tonight_hours_left", "science_fraction", "required_fraction_left")]
    x += [num(g["baseline"].get(k)) for k in BASE_FIELDS]
    requests = g["active_requests"]
    x += [len(requests), sum(num(r.get("completion_reward")) for r in requests) / 1000]
    x += stats([(datetime.fromisoformat(r["deadline_utc"].replace("Z", "+00:00")).timestamp() - now) / 86400 for r in requests])
    x += stats([b["fraction"] for b in g["bands"]])
    for name in ("potential", "deep", "required_left", "future"):
        x += stats([num(r[name]) for r in g["regions"]])
    fault = g.get("faults") or {}
    x += [num(fault.get("report_score")), float(fault.get("report_score") is not None), num(fault.get("recommend_report"))]
    kinds = ("cloudy", "rainy", "earthquake", "terrain_obstruction", "rocket_launch", "instrument_fault")
    notices = (g.get("bulletin") or {}).get("notices", [])
    x += [sum(n.get("event_kind") == k for n in notices) for k in kinds]
    last = g.get("last_result") or {}
    x += [num(last.get("assigned_count")), num(last.get("hit_count")), num(last.get("score_delta")) / 1000,
          sum(m.get("record_type") == "state_resync" for m in g.get("new_messages", []))]
    candidates = []
    for candidate in frame["candidates"]:
        f = candidate["features"]
        c = [num(f[k]) for k in CANDIDATE_FIELDS]
        c += position(f["ra_deg"], f["dec_deg"])
        c += [float(candidate["action"]["program"] == p) for p in ("DARK", "BRIGHT", "BACKUP")]
        targets = f["targets"]
        c += [sum(bool(t["required"]) for t in targets), sum(num(t["science_weight"]) for t in targets) / 1000,
              sum(num(t["best"]) for t in targets) / 1000]
        c += stats([num(t["factor_estimate"]) for t in targets])
        pending = [t["request"] for t in targets if t["request"] is not None]
        c += [len(pending), sum(r[0] for r in pending) / 1000]
        c += stats([(r[1] - now) / 86400 for r in pending])
        conditions = f["conditions"]
        for lead in (900, 1800, 3600):
            row = next((r for r in conditions if str(r["candidate_id"]).endswith(f":{lead}")), {})
            c += [float(bool(row.get("eligible"))), float(bool(row)), float(bool(row.get("limited_history", True)))]
            c += [num(row.get(k)) for k in CONDITION_FIELDS]
        candidates.append(c)
    if not candidates:
        raise ValueError("No candidates")
    return np.asarray(x, np.float32), np.asarray(candidates, np.float32)


class Normalizer:
    def __init__(self, state):
        self.state = state

    @classmethod
    def fit(cls, frames):
        pairs = [encode(f) for f in frames]
        state = {"version": VERSION}
        for key, data in (("global", np.stack([x for x, _ in pairs])), ("candidate", np.concatenate([c for _, c in pairs]))):
            state[key] = {"mean": data.mean(0).tolist(), "std": np.maximum(data.std(0), .01).tolist()}
        return cls(state)

    def apply(self, frame):
        if self.state["version"] != VERSION:
            raise ValueError("Feature version mismatch")
        x, c = encode(frame)
        def transform(a, key):
            cfg = self.state[key]
            return ((a - np.asarray(cfg["mean"], np.float32)) / np.asarray(cfg["std"], np.float32)).astype(np.float32)
        return transform(x, "global"), transform(c, "candidate")
