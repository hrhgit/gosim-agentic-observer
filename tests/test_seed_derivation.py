"""Opt-in hashed seed derivation for scenario generation.

Default (no ``seed_derivation``) must regenerate existing scenarios byte for byte; ``sha256-v1`` must be
deterministic, recorded in the configs and manifest, and give every RNG stream an independent 128-bit seed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from challenge import scenario_builder
from challenge.contracts import (SEED_DERIVATION_HASHED, SEED_DERIVATION_KEY, derive_stream_seed, stream_seed)

# Small anomaly-enabled scenario, file digests captured on main (a5b0005) before the opt-in mode existed.
GOLDEN_DEFAULT = {
    "config/calendar_config.json": "82709dfa37fa4157c2f2709702183f35257873a7dfe18ba4ce6b0ae8bbd033e2",
    "config/request_config.json": "47948e026a17fbef988a05e6a3b8f6742d21d16f732cb3241c5f0876bd0243f8",
    "config/scenario_config.json": "8b824a8fd23448517df3dbea68ccf32ab6a21349a06c68ead2d2ab6e64f78908",
    "config/score_config.json": "10ea90e736270b5fc5807008926627315474b747c56e1998ab845e8fcfdb944a",
    "config/tile_config.json": "0a1ed50f6f52b6a5b6fbfb8f90117415cdcfeb728ef4d5c0b0013393eb20ab3d",
    "config/weather_config.json": "50f0558b961de6cb2bc7da706d37be5053241adbd262840dbbb35c440de39d1a",
    "config/workflow_config.json": "89b6a7954acb081bf01073223d6550359dac1f24fe94d439eb6d6c803851a66e",
    "outputs/reference/night_calendar.csv": "11c1dce7f067178191270342905dbd235802a5b1d7f2911503a167afb27d8685",
    "outputs/reference/observation_request_tiles.csv": "b8902e4ad0acba453a75a1061ef19f4178a9bbb9774a4d65de94a01e1802ff32",
    "outputs/reference/observation_requests.csv": "0e2a562a000ef8e7b27cc665296f34595186ee1d953b01115e8a69fe27ffcad6",
    "outputs/reference/slots.csv": "43adcd8e33dbb06ca0bdf3fb3334ddbf1d3a521fd48b2455ce3fbf61a58ab6ea",
    "outputs/reference/targets.csv": "78adca3d6e1136f146239acda8f93c694cf0325302534a1ece64f298e553f240",
    "outputs/reference/tile_anomalies.csv": "f01e4d736ba7693875f8ef275b50ae263459a5d5757d47486f969a29d8cabcf5",
    "outputs/reference/tile_windows.csv": "d635b634f34dd717b52e30c4369c5bb1b2b959f3b7a47ef934c93165d740e6b3",
    "outputs/reference/tiles.csv": "086f93e9bd0a412c91ae2b1927beeb6d8fafb217b0c506e2ae71a4b1c4c7eba0",
    "outputs/reference/weather.csv": "d834729fdb769d9b04360aeb33c3710e267de8874df3c564eb84b912dca819af",
    "outputs/reference/weather_events.csv": "c65b8455ecb84cb46781eb063f4062f9d54c4d016e6bb083a72c4450f3268995",
    "outputs/reference/weather_forecasts.csv": "92557392386b31a486baba123326ab6eba87407b9d1914f179c144032dfb67fe",
}
STREAMS = {"tiles": 0, "tile_tags": 4000, "weather.slots": 1000, "weather.events": 2000,
           "weather.forecasts": 3000, "weather.forecast_misses": 3100, "requests": 4000}
SEEDED_CONFIGS = ("scenario_config.json", "weather_config.json", "request_config.json")  # tile config: exact key set


def _generate(root: Path, seed: int, **kwargs) -> dict:
    scenario_builder.generate_scenario(
        root, scenario_id="seed-derivation-test", seed=seed, days=7, start_date="2026-10-05", global_wallclock_seconds=60,
        tile_overrides={"n_regions": 8, "tiles_per_region": 12}, coverage_bonus_weight=0.35,
        anomaly_overrides={"nova_count": 3, "reddening_count": 3}, **kwargs)
    return json.loads((root / "outputs/reference/scenario_manifest.json").read_text(encoding="utf-8"))


def _digests(manifest: dict) -> dict:
    return {name: entry["sha256"] for name, entry in manifest["files"].items()}


def test_default_mode_is_byte_identical_to_the_legacy_generator(tmp_path):
    manifest = _generate(tmp_path / "golden", 99)
    # scenario_id differs from the golden run only in scenario_config.json
    digests = _digests(manifest)
    assert {k: v for k, v in digests.items() if k != "config/scenario_config.json"} == \
        {k: v for k, v in GOLDEN_DEFAULT.items() if k != "config/scenario_config.json"}
    assert SEED_DERIVATION_KEY not in manifest
    for name in SEEDED_CONFIGS:
        assert SEED_DERIVATION_KEY not in json.loads((tmp_path / "golden/config" / name).read_text(encoding="utf-8"))


def test_default_scenario_config_matches_golden(tmp_path):
    scenario_builder.generate_scenario(
        tmp_path / "g", scenario_id="golden-default", seed=99, days=7, start_date="2026-10-05", global_wallclock_seconds=60,
        tile_overrides={"n_regions": 8, "tiles_per_region": 12}, coverage_bonus_weight=0.35,
        anomaly_overrides={"nova_count": 3, "reddening_count": 3})
    manifest = json.loads((tmp_path / "g/outputs/reference/scenario_manifest.json").read_text(encoding="utf-8"))
    assert _digests(manifest) == GOLDEN_DEFAULT


def test_stream_seed_default_is_seed_plus_offset():
    for stream, offset in STREAMS.items():
        assert stream_seed({"seed": 12345}, stream, offset) == 12345 + offset
        assert stream_seed({"seed": 12345}, stream, offset, SEED_DERIVATION_HASHED) == derive_stream_seed(12345, stream)


def test_hashed_streams_are_independent_128_bit_seeds():
    seed = 2**127 + 12345
    config = {"seed": seed, SEED_DERIVATION_KEY: SEED_DERIVATION_HASHED}
    values = {stream: stream_seed(config, stream, offset) for stream, offset in STREAMS.items()}
    # tile_tags and requests share the legacy offset 4000; hashed streams must not collide
    assert len(set(values.values())) == len(STREAMS)
    for stream, value in values.items():
        assert value == int.from_bytes(hashlib.sha256(f"{seed}:{stream}".encode()).digest()[:16], "big")
        assert 0 <= value < 2**128
        assert value != seed + STREAMS[stream]
    # no additive relation between streams (unlike seed + k)
    assert values["weather.events"] - values["weather.slots"] != 1000
    # the master seed's high bits matter
    assert derive_stream_seed(seed, "tiles") != derive_stream_seed(seed - 2**127, "tiles")


def test_unknown_derivation_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        stream_seed({"seed": 1, SEED_DERIVATION_KEY: "md5"}, "tiles", 0)
    with pytest.raises(scenario_builder.ScenarioError):
        _generate(tmp_path / "bad", 1, seed_derivation="md5")


def test_hashed_mode_is_deterministic_recorded_and_differs_from_default(tmp_path):
    seed = 2**127 + 99
    first = _generate(tmp_path / "a", seed, seed_derivation=SEED_DERIVATION_HASHED)
    second = _generate(tmp_path / "b", seed, seed_derivation=SEED_DERIVATION_HASHED)
    legacy = _generate(tmp_path / "c", seed)
    assert first == second
    assert (tmp_path / "a/outputs/reference/scenario_manifest.json").read_bytes() == \
        (tmp_path / "b/outputs/reference/scenario_manifest.json").read_bytes()
    assert first[SEED_DERIVATION_KEY] == SEED_DERIVATION_HASHED and first["seed"] == seed
    for name in SEEDED_CONFIGS:
        cfg = json.loads((tmp_path / "a/config" / name).read_text(encoding="utf-8"))
        assert cfg[SEED_DERIVATION_KEY] == SEED_DERIVATION_HASHED and cfg["seed"] == seed
    tile_cfg = json.loads((tmp_path / "a/config/tile_config.json").read_text(encoding="utf-8"))
    assert SEED_DERIVATION_KEY not in tile_cfg and tile_cfg["seed"] == seed
    catalog_meta = json.loads((tmp_path / "a/outputs/reference/catalog_metadata.json").read_text(encoding="utf-8"))
    assert catalog_meta[SEED_DERIVATION_KEY] == SEED_DERIVATION_HASHED
    for name in ("tiles.csv", "weather.csv", "weather_events.csv", "weather_forecasts.csv", "tile_anomalies.csv"):
        key = f"outputs/reference/{name}"
        assert first["files"][key]["sha256"] != legacy["files"][key]["sha256"], name
    info = scenario_builder.describe_scenario(tmp_path / "a")  # authoritative scorer accepts it
    assert info["seed_derivation"] == SEED_DERIVATION_HASHED and info["n_anomaly_tags"] == 6
