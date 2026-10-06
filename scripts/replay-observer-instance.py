#!/usr/bin/env python3
"""Organizer-only verification from a private instance record and committed CSV.

Run from the exact trusted control revision recorded in observer_jobs.workflow_sha.
Neither the seed nor the hidden generated scenario is printed or exported.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from challenge.scoring_core import score_files
from project_platform.scenario_instances import (
    GENERATOR_VERSION, InstanceError, calibrated_score, generate_candidate, measure_difficulty,
)


def replay(template: Path, record: dict, decisions: Path) -> dict:
    if record.get('generator_version') != GENERATOR_VERSION:
        raise InstanceError('replay_generator_version_mismatch')
    with tempfile.TemporaryDirectory(prefix='observer-private-replay-') as temp:
        root = Path(temp)
        scenario = root / 'scenario'
        actual = generate_candidate(template, scenario, seed=record['seed'], candidate=record['candidate'])
        for field in ('template_digest', 'instance_digest'):
            if actual[field] != record[field]:
                raise InstanceError('replay_digest_mismatch')
        difficulty = measure_difficulty(scenario)
        if difficulty != record['difficulty']:
            raise InstanceError('replay_calibration_mismatch')
        report = score_files(scenario, decisions, root / 'score.json')
        raw = report['score']['total']
        return {'instance_commitment': record['instance_digest'], 'raw_score': raw,
                'calibrated_score': calibrated_score(raw, difficulty), 'verified': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--template', type=Path, required=True)
    parser.add_argument('--record', type=Path, required=True)
    parser.add_argument('--decisions', type=Path, required=True)
    args = parser.parse_args()
    result = replay(args.template, json.loads(args.record.read_text()), args.decisions)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
