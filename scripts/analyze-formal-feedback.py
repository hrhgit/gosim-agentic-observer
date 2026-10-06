#!/usr/bin/env python3
"""Analyze contestant result ZIPs only; never load organizer scenario truth.

python3 scripts/analyze-formal-feedback.py --input artifacts/formal-feedback-2026-10-05
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import statistics
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def notice_episodes(bulletins: list[dict]) -> dict:
    """Contiguous presence in published slot order; skip daytime gaps.

    A kind/direction pair can merge multiple underlying events. This measures
    public episodes, NOT the number of hidden events. Forecasts are excluded.
    """
    active: dict[tuple[str, str], dict] = {}
    episodes: dict[str, list[dict]] = defaultdict(list)
    for bulletin in sorted(bulletins, key=lambda b: b["issued_at_utc"]):
        current = {(n["event_kind"], n["direction"]) for n in bulletin["notices"]}
        for key in sorted(set(active) - current):
            episodes[key[0]].append(active.pop(key))
        for key in sorted(current):
            if key not in active:
                active[key] = {"direction": key[1], "first_public_slot_utc": bulletin["issued_at_utc"],
                               "last_public_slot_utc": bulletin["issued_at_utc"], "published_slots": 0}
            active[key]["published_slots"] += 1
            active[key]["last_public_slot_utc"] = bulletin["issued_at_utc"]
    for key in sorted(active):
        episodes[key[0]].append(active[key])
    return dict(sorted(episodes.items()))


def distribution(values: list[float]) -> dict:
    values = sorted(values)
    if not values:
        return {"count": 0}
    return {"count": len(values), "min": values[0], "p10": values[int((len(values)-1)*.1)],
            "median": statistics.median(values), "p90": values[int((len(values)-1)*.9)],
            "max": values[-1]}


def request_shape(request: dict, nights: list[dict]) -> dict:
    start, end = request["issued_at_utc"], request["deadline_utc"]
    touched = sum(n["observing_start_utc"] < end and n["observing_end_utc"] > start for n in nights)
    return {"targets": len(request["target_ids"]), "minimum_completed": request["minimum_completed"],
            "reward": request["completion_reward"], "factor_threshold": request["completion_factor_threshold"],
            "window_nights": touched,
            "window_hours": (datetime.fromisoformat(end.replace("Z", "+00:00")) -
                             datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds()/3600}


def analyze(paths: list[Path]) -> dict:
    output = {"schema_version": "contestant-feedback-analysis-v1", "archives": [], "cards": {},
              "scope": "Official formal A-D contestant feedback only; no hidden truth or practice cards read.",
              "limitations": ["Three executions of one revision are not independent weather samples.",
                              "Public episodes may merge overlapping same-kind/same-direction events.",
                              "Successful repairs are a lower bound on fault count, not total fault count.",
                              "Quality is sampled on this agent's chosen targets; it is not the whole sky distribution.",
                              "Weather severity, exact sectors, closure probabilities and undetected faults remain unknown."]}
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            meta = json.loads(archive.read("evaluation.json"))
            output["archives"].append({"file": path.name, "sha256": digest(path.read_bytes()),
                                       "evaluation": meta})
            for folder in sorted({name.split("/")[0] for name in archive.namelist() if name.endswith("/score_report.json")}):
                report = json.loads(archive.read(folder + "/score_report.json"))
                card = report["scenario"].removeprefix("v4-")
                if card not in "abcd" or len(card) != 1:
                    raise ValueError(f"Not a formal card: {report['scenario']}")
                for filename, key in [("decisions.csv", "decisions"), ("observations.csv", "observations"),
                                      ("messages.jsonl", "messages")]:
                    if digest(archive.read(folder + "/" + filename)) != report["sha256"][key]:
                        raise ValueError(f"Result hash mismatch: {path}/{folder}/{filename}")
                messages = [json.loads(line) for line in archive.read(folder + "/messages.jsonl").decode().splitlines()]
                decisions = list(csv.DictReader(io.StringIO(archive.read(folder + "/decisions.csv").decode())))
                observations = list(csv.DictReader(io.StringIO(archive.read(folder + "/observations.csv").decode())))
                record_types = Counter(m["record_type"] for m in messages)
                repairs = [m for m in messages if m["record_type"] == "report_result"]
                assert abs(sum(report["components"].values()) - report["total"]) < 0.00001
                assert sum(m["score_delta"] for m in repairs) == report["components"]["report_settlement"]
                assert len(decisions) == report["counts"]["decisions"]
                assert len(observations) == report["counts"]["observations"]
                nights = list(csv.DictReader((ROOT/"cards"/card/"public/v4_night_calendar.csv").open()))
                reqs = report["observation_requests"]
                workflow = json.loads(archive.read(folder+"/workflow_result.json"))
                run = {"evaluation_id": meta["evaluation_id"], "score": report["total"],
                       "components": report["components"], "counts": report["counts"],
                       "termination": report["termination"], "record_counts": dict(record_types),
                       "successful_repairs": sum(m["correct"] for m in repairs), "reports": repairs,
                       "requests": [{"request_id": q["request_id"], **request_shape(q, nights),
                                     "completed": q["completed"], "completed_count": q["completed_count"]} for q in reqs],
                       "invalidations": report["invalidations"],
                       "state_resync": [{"issued_at_utc": m["issued_at_utc"],
                                         "invalidated_window": m["invalidated_window"]} for m in messages
                                        if m["record_type"] == "state_resync"],
                       "quality_on_chosen_targets": distribution([float(o["quality"]) for o in observations]),
                       "zero_quality_fraction": sum(float(o["quality"]) == 0 for o in observations)/len(observations),
                       "assigned_to_hit_fraction": sum(int(d["hit_count"]) for d in decisions)/
                                                   sum(int(d["assigned_count"]) for d in decisions),
                       "accounted_wallclock_seconds": workflow.get("accounted_wallclock_seconds")}
                entry = output["cards"].setdefault(card, {"runs": [], "_bulletins": {}, "_forecasts": {}, "_requests": {}})
                entry["runs"].append(run)
                for kind, index in [("bulletin", "slot_id"), ("forecast", "issued_at_utc"), ("observation_request", "request_id")]:
                    target = entry["_" + {"bulletin":"bulletins", "forecast":"forecasts", "observation_request":"requests"}[kind]]
                    for msg in (m for m in messages if m["record_type"] == kind):
                        key = msg[index]
                        if key in target and target[key] != msg:
                            raise ValueError(f"Different public inputs across repetitions: {card}/{kind}/{key}")
                        target[key] = msg
    for card, entry in output["cards"].items():
        episodes = notice_episodes(list(entry.pop("_bulletins").values()))
        entry["public_event_episodes"] = episodes
        entry["public_event_summary"] = {kind: {"episodes": len(rows),
              "published_slots": sum(r["published_slots"] for r in rows),
              "duration_in_published_slots": distribution([r["published_slots"] for r in rows]),
              "global_episodes": sum(r["direction"] == "ALL" for r in rows),
              "directional_episodes": sum(r["direction"] != "ALL" for r in rows)} for kind, rows in episodes.items()}
        entry["unique_forecasts"] = len(entry.pop("_forecasts"))
        entry["unique_requests"] = len(entry.pop("_requests"))
        entry["fault_count_lower_bound"] = max(r["successful_repairs"] for r in entry["runs"])
        entry["score"] = distribution([r["score"] for r in entry["runs"]])
        entry["same_public_inputs_across_repetitions"] = True
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    paths = [p for p in sorted(args.input.glob("*.zip")) if p.name != "evaluated-project.zip"]
    if not paths:
        raise SystemExit("No result archives")
    result = analyze(paths)
    destination = args.input/"feedback-analysis.json"
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)+"\n")
    print(json.dumps({"analysis":str(destination), "archives":len(paths),
                      "cards":{c:{"score_mean":statistics.mean(r["score"] for r in e["runs"]),
                                 "public_episodes":{k:v["episodes"] for k,v in e["public_event_summary"].items()},
                                 "fault_lower_bound":e["fault_count_lower_bound"]} for c,e in result["cards"].items()}},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
