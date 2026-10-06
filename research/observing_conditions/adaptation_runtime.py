"""Native / constrained Linux measurement, without simulator or hidden labels."""
from __future__ import annotations
import argparse
import gzip
import importlib.metadata
import json
import platform
from pathlib import Path
import resource
import time
import numpy as np
import torch
from challenge.fair_clock import SpeedGauge
from .adaptation_data import DEFAULT
from .adaptation_predict import AdaptationPredictor
from .adaptation_model import infer_gru
from .information_model import calibration_apply,NAMES
from .data import save_json
from .features import utc


def measure(directory=DEFAULT,output=None):
    directory=Path(directory);torch.set_num_threads(1)
    gauge=SpeedGauge();speed=gauge.sample(5)
    predictor=AdaptationPredictor(directory)
    request=json.loads((directory/'example_request.json').read_text())
    expected=json.loads((directory/'example_prediction.json').read_text())
    actual=predictor.predict(request)
    differences=[]
    for a,b in zip(actual['predictions'],expected['predictions']):
        assert a['eligible']==b['eligible']
        if a['eligible']:differences.extend(abs(a[k]-b[k]) for k in NAMES)
    assert max(differences,default=0)<1e-5
    with np.load(directory/'runtime_public_inputs.npz') as f:
        assert set(f.files)=={'x','history'}
        arrays={k:f[k][np.arange(1024)%len(f[k])] for k in f.files}
    pure=[]
    for i in range(65):
        started=time.perf_counter()
        p=predictor.online.predict(arrays)
        calibration_apply(p,predictor.frozen['calibration'][predictor.method])
        if i>=5:pure.append((time.perf_counter()-started)*1000)
    batch_request=dict(request,candidates=[dict(request['candidates'][i%len(request['candidates'])],candidate_id=str(i)) for i in range(1024)])
    end_to_end=predictor.predict(batch_request)
    init=json.loads((directory/'runtime_initialize.json').read_text())
    bulletin=[json.loads(v) for v in (directory/'runtime_bulletins.jsonl').read_text().splitlines()]
    forecast=[json.loads(v) for v in (directory/'runtime_forecasts.jsonl').read_text().splitlines()]
    predictor.reset();cpu=time.process_time();wall=time.perf_counter();exposures=0;proxy_rows=0
    published_b=[];published_f=[];bi=fi=0
    with gzip.open(directory/'runtime_public_exposures.jsonl.gz','rt') as f:
        for line in f:
            r=json.loads(line);now=utc(r['end_utc'])
            while bi<len(bulletin) and utc(bulletin[bi]['issued_at_utc'])<=now:published_b.append(bulletin[bi]);bi+=1
            while fi<len(forecast) and utc(forecast[fi]['issued_at_utc'])<=now:published_f.append(forecast[fi]);fi+=1
            u=predictor.update(r,init,published_b,published_f,r['end_utc']);exposures+=1;proxy_rows+=u['public_proxy_rows']
            if exposures%1000==0:print('public replay',exposures,'updates',u['parameter_updates'],flush=True)
    total_cpu=time.process_time()-cpu;total_wall=time.perf_counter()-wall
    cgroup={}
    for name in ('memory.max','memory.peak','cpu.max','pids.max'):
        path=Path('/sys/fs/cgroup')/name
        if path.exists():cgroup[name]=path.read_text().strip()
    rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_bytes=int(rss if platform.system()=='Darwin' else rss*1024)
    result=dict(platform=platform.platform(),architecture=platform.machine(),torch_threads=torch.get_num_threads(),
        dependency_versions={k:importlib.metadata.version(k) for k in ('torch','numpy','scipy')},
        pure_inference=dict(candidates=1024,p50_ms=float(np.median(pure)),p95_ms=float(np.quantile(pure,.95)),repeats=60,
            boundary='correction + numeric conversion + calibration; frozen foundation/feature construction excluded'),
        full_input_batch=dict(candidates=1024,**end_to_end['timing']),numeric_max_difference=max(differences,default=0),
        full_card_public_updates=dict(card='B',exposures=exposures,proxy_rows=proxy_rows,updates=predictor.online.updates,
            update_only_cpu_seconds=predictor.online.cpu_seconds,preprocessing_and_update_cpu_seconds=total_cpu,
            wall_seconds=total_wall,approximate_charged_seconds=total_cpu/speed,
            limitation='Measured standalone public replay, not server cgroup charged time; Python calibration may not track tensor speed'),
        fair_clock_speed_factor=speed,max_rss_bytes=rss_bytes,cgroup=cgroup,
        scope='Model-only inference/update validation; no agent competition run or submission')
    if output:save_json(output,result)
    print(json.dumps(result),flush=True);return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--directory',type=Path,default=DEFAULT)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();measure(args.directory,args.output)
