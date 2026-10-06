"""Independent Python/Node numerical checks, using formal public A-D + self-generated messages."""
from __future__ import annotations

import argparse
import copy
from datetime import timedelta
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[3]
AGENT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'artifacts/observing_conditions-information'
sys.path.insert(0, str(ROOT))


def requests():
    from research.observing_conditions.features import utc
    from research.observing_conditions.information_regions import representatives
    for card in 'abcd':
        path = AGENT / f'results/feedback-nominal-v3/{card}/inputs.jsonl'
        with path.open() as stream:
            initialize = json.loads(next(stream))['payload']
        public = {key: initialize[key] for key in ('site', 'scoring', 'instrument', 'survey')}
        night = public['survey']['nights'][0]
        now = utc(night['observing_start_utc']) + timedelta(seconds=7200)
        bulletins = [dict(record_type='bulletin', issued_at_utc=(now-timedelta(seconds=(40-i)*900)).isoformat(),
            notices=[dict(event_kind=('haze', 'overcast', 'rain', 'terrain_obstruction')[i % 4],
                          direction=('N', 'SW', 'ALL', 'NW')[i % 4])]) for i in range(40)]
        forecasts = [dict(record_type='forecast', issued_at_utc=(now-timedelta(days=1)).isoformat(),
            coverage_start_utc=(now-timedelta(days=2)).isoformat(), coverage_end_utc=(now+timedelta(days=4)).isoformat(),
            notices=[dict(event_kind='cold_snap', direction='NE', nights=[night['night_date']])])]
        for mode in ('history', 'missing', 'tail'):
            decision = utc(night['observing_end_utc'])-timedelta(seconds=120) if mode == 'tail' else now
            candidates = []
            for lead in (0, 900, 1800, 3600):
                start = decision + timedelta(seconds=lead)
                _, coordinates, _, _ = representatives(public['site'], start)
                for i, (ra, dec) in enumerate(coordinates):
                    candidates.append(dict(candidate_id=f'{lead}:{i}', ra_deg=float(ra), dec_deg=float(dec),
                        start_utc=start.isoformat(), duration_seconds=(60, 300, 900, 3600)[i % 4]))
            # Deliberately repeated inputs/ids and low-altitude locations must all be retained.
            candidates += [copy.deepcopy(candidates[0]), dict(candidate_id='low', ra_deg=0, dec_deg=-89,
                            start_utc=decision.isoformat(), duration_seconds=300)]
            yield dict(initialize=public, now_utc=decision.isoformat(),
                bulletins=[] if mode == 'missing' else bulletins,
                forecasts=[] if mode == 'missing' else forecasts, candidates=candidates)


def verify(node, output):
    from research.observing_conditions.information_predict import InformationPredictor
    from research.observing_conditions.features import PublicTimeline, candidate_features, utc
    torch.set_num_threads(1)
    output.mkdir(parents=True, exist_ok=True)
    cases = list(requests())
    (output / 'requests.jsonl').write_text(''.join(json.dumps(case) + '\n' for case in cases))
    completed = subprocess.run([node, str(AGENT / 'dist/tools/predict-models.js')],
        input=''.join(json.dumps(case)+'\n' for case in cases), capture_output=True, text=True, check=True)
    actual = [json.loads(line)['models'] for line in completed.stdout.splitlines()]
    (output / 'typescript.jsonl').write_text(completed.stdout)
    frozen = json.loads((SOURCE / 'frozen.json').read_text())
    from research.observing_conditions.information_model import NAMES
    stats = {}
    with tempfile.TemporaryDirectory(prefix='gosim-model-parity-') as temporary:
        for arch in ('gru', 'mlp'):
            directory = Path(temporary) / arch; directory.mkdir()
            for name in ('weights.pt', 'normalizer.npz'):
                shutil.copyfile(SOURCE / 'models' / arch / name, directory / name)
            metadata = json.loads((SOURCE / 'models' / arch / 'metadata.json').read_text()) | dict(
                feature_version='public-env-features-v1', calibration=frozen['point_calibration'][arch],
                source='formal public inputs + explicitly self-generated messages', quality_definition='Q_env',
                validation_reliable=False, final_test_passed=None)
            (directory / 'metadata.json').write_text(json.dumps(metadata))
            model = InformationPredictor(directory)
            error, values, eligible, maximum_probability_sum_error = 0., 0, 0, 0.
            for i, request in enumerate(cases):
                expected = model.predict(request)['predictions']
                observed = next(m for m in actual[i] if m['architecture'] == arch)['predictions']
                assert len(expected) == len(observed) == len(request['candidates'])
                for a, b in zip(expected, observed):
                    assert a['candidate_id'] == b['candidate_id'] and a['eligible'] == b['eligible']
                    assert a.get('reason') == b.get('reason')
                    if not a['eligible']: continue
                    eligible += 1
                    for field in (*NAMES, 'known_geometry_factor', 'actual_duration_seconds'):
                        difference = abs(a[field]-b[field]); error = max(error, difference); values += 1
                        if not np.isclose(a[field], b[field], atol=1e-5, rtol=1e-5):
                            raise AssertionError(f'{arch} case {i} candidate {a["candidate_id"]} {field}: {a[field]} vs {b[field]}')
                    assert 0 <= b['u05'] <= b['u50'] <= b['u95'] <= 1
                    assert 0 <= b['q05'] <= b['q50'] <= b['q95']
                    maximum_probability_sum_error = max(maximum_probability_sum_error, abs(sum(b[k] for k in NAMES[-3:])-1))
            stats[arch] = dict(eligible_predictions=eligible, compared_values=values, maximum_absolute_difference=error,
                               maximum_probability_sum_error=maximum_probability_sum_error)
    # Check public feature extraction separately to avoid hidden compensating errors.
    probe = """const {prepareConditions}=require('./dist/models');
const {createInterface}=require('node:readline');
createInterface({input:process.stdin}).on('line',s=>{const p=prepareConditions(JSON.parse(s));
 console.log(JSON.stringify(p.groups.map(g=>({context:Array.from(g.context),history:g.history.map(h=>Array.from(h)),
 candidates:g.candidates.map(c=>Array.from(c)),indices:g.indices}))));});"""
    feature_run = subprocess.run([node, '-e', probe], cwd=AGENT, input=''.join(json.dumps(c)+'\n' for c in cases),
                                 text=True, capture_output=True, check=True)
    feature_error = 0.
    for case, groups in zip(cases, map(json.loads, feature_run.stdout.splitlines())):
        initialize = case['initialize']; timeline = PublicTimeline(case['bulletins'], case['forecasts'])
        for group in groups:
            if not group['indices']: continue
            candidate = case['candidates'][group['indices'][0]]; start=utc(candidate['start_utc'])
            night = next(n for n in initialize['survey']['nights'] if utc(n['observing_start_utc'])<=start<utc(n['observing_end_utc']))
            end = min(start+timedelta(seconds=candidate['duration_seconds']),utc(night['observing_end_utc']))
            context, history = timeline.snapshot(case['now_utc'], start, candidate['duration_seconds'], (end-start).total_seconds(),
                night['night_date'], night['observing_end_utc'], initialize['site'], initialize['scoring'])
            coordinates=np.array([[case['candidates'][ix]['ra_deg'],case['candidates'][ix]['dec_deg']] for ix in group['indices']])
            candidate_x,_=candidate_features(coordinates[:,0],coordinates[:,1],start,end,initialize['site'],initialize['scoring'])
            for expected, observed in ((context,group['context']),(history,group['history']),(candidate_x,group['candidates'])):
                feature_error=max(feature_error,float(np.max(np.abs(expected-np.array(observed)))))
                np.testing.assert_allclose(expected, observed, atol=1e-6, rtol=1e-6)
    summary = dict(source='Formal A-D public initialize + explicitly self-generated notices, forecasts and positions; no official hidden data',
                   node=subprocess.check_output([node,'--version'],text=True).strip(), cases=len(cases),
                   candidates=sum(len(c['candidates']) for c in cases), models=stats, maximum_feature_difference=feature_error,
                   weights_retrained=False, passed=True)
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node',required=True)
    parser.add_argument('--out',type=Path,default=AGENT/'results/models-parity')
    args=parser.parse_args()
    verify(args.node,args.out)
