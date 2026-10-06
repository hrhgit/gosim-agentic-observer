"""Paired full-public-request equivalence and timing, without retraining."""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import numpy as np
import torch

from challenge.v4_fiber_map import altaz_to_radec
from .features import utc
from .predict import Predictor,OUTPUT_NAMES
from .data import DEFAULT_OUTPUT,save_json
from .improvement import DEFAULT_IMPROVEMENT


def compare(before,after,request):
    a=before.predict(request);b=after.predict(request)
    error=0.
    for left,right in zip(a['predictions'],b['predictions']):
        assert left['candidate_id']==right['candidate_id'] and left['eligible']==right['eligible']
        if not left['eligible']:
            assert left['reason']==right['reason']
        else:
            x=np.array([left[k] for k in OUTPUT_NAMES]);y=np.array([right[k] for k in OUTPUT_NAMES])
            error=max(error,float(np.abs(x-y).max()))
            np.testing.assert_allclose(x,y,atol=2e-6,rtol=2e-6)
    return error


def run(base=DEFAULT_OUTPUT,output=DEFAULT_IMPROVEMENT):
    base,output=Path(base),Path(output)
    from . import scalar_reference as legacy
    torch.set_num_threads(1)
    before=legacy.Predictor(base/'models/selected');after=Predictor(base/'models/selected')
    original=json.loads((base/'example_request.json').read_text())
    repeated=copy.deepcopy(original)
    repeated['candidates']=[dict(c,candidate_id=f'repeat-{i}') for i,c in enumerate(original['candidates']*32)]
    distinct=copy.deepcopy(repeated)
    start=utc(original['candidates'][0]['start_utc']);site=original['initialize']['site']
    rng=np.random.default_rng(101)
    for c in distinct['candidates']:
        ra,dec=altaz_to_radec(float(rng.uniform(35,88)),float(rng.uniform(0,360)),start,
            site['latitude_deg'],site['longitude_deg'])
        c.update(ra_deg=ra,dec_deg=dec)
    multiple=copy.deepcopy(repeated)
    from datetime import timedelta
    now=utc(original['now_utc'])
    for i,c in enumerate(multiple['candidates']):
        c['start_utc']=(now+timedelta(seconds=(0,900,1800,3600)[i%4])).isoformat()
    result=dict(boundary='public input processing plus network and calibration; excludes JSON decoding '
        'and model initialization; same old weights and calibration before/after',threads=1,repetitions=10,
        warning='Ten observed samples; p95 is an empirical timing quantile, not a stable latency guarantee.',cases={})
    for name,request in (('32_distinct_repeated_to_1024',repeated),('1024_distinct',distinct),('four_time_groups_1024',multiple)):
        error=compare(before,after,request)
        timings={'before':[],'after':[]}
        for index in range(10):
            for method,model in (('before',before),('after',after)):
                begin=time.perf_counter();model.predict(request)
                timings[method].append((time.perf_counter()-begin)*1000)
        result['cases'][name]=dict(candidates=1024,max_numeric_difference=error,
            timings_ms={k:dict(p50=float(np.median(v)),p95=float(np.quantile(v,.95)),samples=v) for k,v in timings.items()})
        print('benchmark '+name+' '+json.dumps(result['cases'][name]['timings_ms']),flush=True)
    save_json(output/'batching_benchmark.json',result)
    return result


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--base',type=Path,default=DEFAULT_OUTPUT)
    parser.add_argument('--output',type=Path,default=DEFAULT_IMPROVEMENT)
    args=parser.parse_args();run(args.base,args.output)
