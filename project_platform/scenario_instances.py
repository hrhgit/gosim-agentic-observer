"""Private, reproducible scenario generation and versioned difficulty calibration.

Only the trusted engine may import this module. A seed and this module's records
must never be placed in participant artifacts, observations or project jobs.
The catalog, calendar, requests and scoring contract remain fixed; weather,
forecasts, events and the assignment of hidden tags vary independently.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import os
import shutil
from concurrent.futures import ProcessPoolExecutor
from collections import OrderedDict
from datetime import timedelta
from pathlib import Path
from statistics import fmean

from challenge.challenge_workflow import ChallengeWorkflow
from challenge.contracts import read_exact_csv, write_exact_csv, TILE_COLUMNS
from challenge.scenario_builder import build_manifest, generate_tile_anomalies, scenario_files
from challenge.scoring_preview import preview_actions
from challenge.scoring_core import Decision
from challenge.weather_simulator import generate as generate_weather, load_weather

GENERATOR_VERSION = "observer-weather-instance-v1"
PANEL_VERSION = "observer-reference-panel-v1"
POLICIES = ("gain_rate", "required_first", "requests_first")
# Not used when accepting scenarios: an independent comparison for calibration
# reports. It deliberately values immediate science over completion penalties.
HOLDOUT_POLICY = "science_first"
_WINDOW_CACHE: OrderedDict[tuple[str, str], list[dict]] = OrderedDict()


class InstanceError(ValueError):
    pass


def _subseed(seed: str, candidate: int, purpose: str) -> int:
    if not isinstance(seed, str) or not re.fullmatch(r"[0-9a-f]{64}", seed):
        raise InstanceError("invalid_private_seed")
    if type(candidate) is not int or not 0 <= candidate < 1000:
        raise InstanceError("invalid_candidate_index")
    message = f"{GENERATOR_VERSION}/{candidate}/{purpose}".encode()
    return int.from_bytes(hmac.digest(bytes.fromhex(seed), message, "sha256"), "big")


def directory_digest(root: Path) -> str:
    """Digest file names and bytes, independent of ZIP metadata and host paths."""
    digest = hashlib.sha256()
    for path in sorted(scenario_files(root)):
        if not path.is_file() or path.is_symlink():
            raise InstanceError("invalid_scenario_file")
        name = path.relative_to(root).as_posix().encode()
        data = path.read_bytes()
        digest.update(len(name).to_bytes(4, "big") + name)
        digest.update(len(data).to_bytes(8, "big") + data)
    return digest.hexdigest()


def generate_candidate(template: Path, destination: Path, *, seed: str, candidate: int) -> dict:
    """Create one private candidate in a NEW directory; never mutate a template."""
    weather_seed = _subseed(seed, candidate, "weather")
    tag_seed = _subseed(seed, candidate, "tags")
    template_digest = directory_digest(template)
    # Refuse an existing directory rather than deleting or overwriting it.
    destination.mkdir(parents=True, exist_ok=False)
    for source in scenario_files(template):
        target = destination / source.relative_to(template)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    config = destination / "config"
    data = destination / "outputs/reference"
    for name, value in (("weather_config.json", weather_seed),
                        ("scenario_config.json", _subseed(seed, candidate, "scenario"))):
        path = config / name
        payload = json.loads(path.read_text())
        payload["seed"] = value
        path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    generate_weather(config / "weather_config.json", data / "night_calendar.csv",
                     data / "slots.csv", data / "tiles.csv", data)
    tile_config = json.loads((config / "tile_config.json").read_text())
    # This seed selects hidden tags only. Keep the original tile config intact:
    # the public catalog was generated with its original seed, not this one.
    tile_config["seed"] = tag_seed
    if (data / "tile_anomalies.csv").exists() or tile_config.get("anomaly_tags"):
        tile_ids = [row["tile_id"] for row in read_exact_csv(data / "tiles.csv", TILE_COLUMNS)]
        write_exact_csv(data / "tile_anomalies.csv", ("tile_id", "anomaly_tag"),
                        generate_tile_anomalies(tile_config, tile_ids))
    build_manifest(destination)
    return {"generator_version": GENERATOR_VERSION, "seed": seed, "candidate": candidate,
            "template_digest": template_digest, "instance_digest": directory_digest(destination)}


def _reuse_geometry_windows(workflow: ChallengeWorkflow, scenario: Path) -> None:
    """Cache ONLY fixed geometric visibility, never weather or observations.

    The official function recomputes the same sky geometry for every policy and
    seed. Reusing its exact output makes the calibration affordable without
    approximating scores. File hashes separate templates; copies prevent a
    caller mutating the cache. The bounded cache holds no private weather data.
    """
    fingerprint = hashlib.sha256()
    for name in ("config/tile_config.json", "config/calendar_config.json", "outputs/reference/tiles.csv",
                 "outputs/reference/slots.csv", "outputs/reference/night_calendar.csv"):
        fingerprint.update(hashlib.sha256((scenario / name).read_bytes()).digest())
    key = fingerprint.hexdigest()
    original = workflow.scorer.geometry.get_tile_windows

    def cached(first_night, days=1):
        if days < 1:
            raise ValueError("days must be positive")
        rows = []
        for offset in range(days):
            night = first_night + timedelta(days=offset)
            cache_key = (key, night.isoformat())
            if cache_key not in _WINDOW_CACHE:
                _WINDOW_CACHE[cache_key] = original(night, 1)
                # A template spans 180 nights. A smaller LRU evicts its first
                # nights before the next policy starts, causing every lookup
                # in that next pass to miss. Cover both official templates.
                if len(_WINDOW_CACHE) > 512:
                    _WINDOW_CACHE.popitem(last=False)
            _WINDOW_CACHE.move_to_end(cache_key)
            rows.extend(dict(row) for row in _WINDOW_CACHE[cache_key])
        return rows

    workflow.scorer.geometry.get_tile_windows = cached


def benchmark_policy(scenario: Path, policy: str, *, reuse_windows: bool = True) -> dict:
    """A deterministic online policy; it only sees current public observations.

    A logical clock avoids selecting different scenarios on faster machines.
    This is trusted bounded calibration code, never a participant deadline.
    """
    if policy not in (*POLICIES, HOLDOUT_POLICY, "wait"):
        raise InstanceError("unknown_calibration_policy")
    workflow = ChallengeWorkflow(scenario, clock=lambda: 0.0)
    if policy == "wait":
        # The all-wait control consumes no observations. Execute its exact
        # decisions in the official scorer without constructing unused sky
        # snapshots for thousands of tiles at each slot.
        count = 0
        while (slot := workflow.scorer.current_slot()) is not None:
            count += 1
            workflow.scorer.apply_decision(Decision(f"D{count:06d}", slot.slot_id, "wait", "", "", "", ""))
        report = workflow.scorer.finalize("survey_complete")
        return {"score": float(report["score"]["total"]),
                "completed_tiles": len(report["completion"]["completed_tiles"]),
                "required_missing": len(report["completion"]["required_missing"])}
    if reuse_windows:
        _reuse_geometry_windows(workflow, scenario)
    contract = workflow.initial_publication()["scoring_contract"]
    mechanics = workflow.scorer.mechanics
    best: dict[str, float] = {}

    def decide(snapshot, _deadline):
        feedback = snapshot.get("tile_last_finished")
        if feedback and feedback["tile_id"] in snapshot["progress"]["completed_tile_ids"]:
            tile = feedback["tile_id"]
            best[tile] = max(best.get(tile, 0.0), feedback["score"])
        # Without the anomaly mechanics a repeat of a completed tile is an
        # invalid duplicate (scoring_core rejects it with a penalty). Passing a
        # realized-best ledger makes the preview offer such repeats, so the
        # panel would spend the survey on penalties and score below waiting
        # (span <= 0, uncalibratable). Scenarios with the mechanics keep the
        # ledger, so their panel scores and existing profiles are unchanged.
        candidates = preview_actions(snapshot, contract, best if mechanics else None)
        if not candidates:
            return {"action": "wait"}
        # Stable sort preserves the public preview's tie-breaking order.
        if policy == "required_first":
            candidates.sort(key=lambda c: (c.terminal_penalty_avoidance if c.scheduling_class == "REQUIRED" else 0,
                                           c.estimated_gain_per_second), reverse=True)
        elif policy == "requests_first":
            candidates.sort(key=lambda c: (c.request_policy_value, c.estimated_gain_per_second), reverse=True)
        elif policy == HOLDOUT_POLICY:
            candidates.sort(key=lambda c: c.estimated_science_score / c.nominal_exptime_seconds, reverse=True)
        chosen = candidates[0]
        return {"action": "observe", "tile_id": chosen.tile_id, "program": chosen.program,
                "request_id": chosen.request_id}

    result = workflow.run(decide)
    if result["termination_reason"] != "survey_complete":
        raise InstanceError("calibration_policy_incomplete")
    report = result["score_report"]
    return {"score": float(report["score"]["total"]),
            "completed_tiles": len(report["completion"]["completed_tiles"]),
            "required_missing": len(report["completion"]["required_missing"])}


def measure_difficulty(scenario: Path, workers: int | None = None) -> dict:
    # The reference policies are independent, deterministic simulations; running
    # them side by side gives identical scores in a fraction of the wall time, so
    # instance preparation fits its deadline even when candidates are rejected.
    names = (*POLICIES, "wait")
    workers = min(len(names), os.cpu_count() or 1) if workers is None else workers
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            scores = dict(zip(names, pool.map(benchmark_policy, [scenario] * len(names), names)))
    else:
        scores = {name: benchmark_policy(scenario, name) for name in names}
    weather = load_weather(scenario / "outputs/reference/weather.csv")
    if not weather:
        raise InstanceError("empty_scenario")
    floor = scores["wait"]["score"]
    reference = fmean(scores[name]["score"] for name in POLICIES)
    span = reference - floor
    if not math.isfinite(span) or span <= 0:
        raise InstanceError("invalid_calibration_span")
    return {"panel_version": PANEL_VERSION, "policies": scores, "wait_score": floor,
            "reference_score": reference, "span": span,
            "open_fraction": sum(row.is_observable for row in weather) / len(weather)}


def calibrated_score(raw_score: float, difficulty: dict) -> float:
    """Wait = 0; the fixed panel mean = 10,000; no cap on better strategies.

    This is an empirical adjustment, not a claim that seeds are equally hard
    for every possible strategy. Acceptance bounds and holdout tests are also
    required. Negative raw totals work without dividing by a negative score.
    """
    floor, span = float(difficulty["wait_score"]), float(difficulty["span"])
    if difficulty.get("panel_version") != PANEL_VERSION or not all(
            math.isfinite(x) for x in (raw_score, floor, span)) or span <= 0:
        raise InstanceError("invalid_calibration")
    return round(10000.0 * (raw_score - floor) / span, 6)


def acceptance_reasons(difficulty: dict, profile: dict) -> list[str]:
    """Apply an organizer-frozen profile, never a participant-selected one."""
    if (profile.get("panel_version") != PANEL_VERSION or
            difficulty.get("panel_version") != PANEL_VERSION):
        raise InstanceError("calibration_version_mismatch")
    failures = []
    for name, bounds in profile["bounds"].items():
        if name == "span":
            value = difficulty["span"]
        elif name == "open_fraction":
            value = difficulty["open_fraction"]
        elif name in POLICIES:
            value = calibrated_score(difficulty["policies"][name]["score"], difficulty)
        else:
            raise InstanceError("unknown_calibration_bound")
        low, high = bounds
        if not all(math.isfinite(v) for v in (value, low, high)) or low > high:
            raise InstanceError("invalid_calibration_bound")
        if not low <= value <= high:
            failures.append(name)
    if set(profile["bounds"]) != {"span", "open_fraction", *POLICIES}:
        raise InstanceError("incomplete_calibration_profile")
    return failures


def prepare_instance(template: Path, destination: Path, *, seed: str, profile: dict,
                     max_candidates: int = 32) -> tuple[Path, dict]:
    """Select the first qualifying candidate, or fail without serving any data.

    Retries reuse the same private seed, frozen template and profile. Candidate
    selection never depends on participant code, results, identity or timing.
    The record belongs in private organizer storage BEFORE initial publication.
    """
    if type(max_candidates) is not int or not 1 <= max_candidates <= 32:
        raise InstanceError("invalid_candidate_limit")
    if (profile.get("schema_version") != "observer-calibration-profile-v1" or
            profile.get("template_digest") != directory_digest(template)):
        raise InstanceError("calibration_template_mismatch")
    profile_digest = hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":"),
                                               allow_nan=False).encode()).hexdigest()
    destination.mkdir(parents=True, exist_ok=False)
    rejected = []
    for index in range(max_candidates):
        candidate = destination / f"candidate-{index:02}"
        record = generate_candidate(template, candidate, seed=seed, candidate=index)
        difficulty = measure_difficulty(candidate)
        reasons = acceptance_reasons(difficulty, profile)
        if not reasons:
            return candidate, {**record, "profile_digest": profile_digest,
                               "difficulty": difficulty, "rejected_candidates": rejected}
        rejected.append({"candidate": index, "reasons": reasons,
                         "instance_digest": record["instance_digest"]})
        # Only remove the exact candidate directory created by this invocation.
        # It contains no participant files or externally supplied directory tree.
        shutil.rmtree(candidate)
    raise InstanceError("no_comparable_scenario")
