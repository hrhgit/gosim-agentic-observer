"""Compare the selected inference adapters with their frozen research sources; no training."""
import json,gzip,os,sys,subprocess,importlib.util,io,time
from pathlib import Path
from datetime import timedelta
import numpy as np
import torch
ROOT=Path(__file__).resolve().parents[3];AGENT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from research.observing_conditions.reliability_predict import ReliabilityPredictor
from research.observing_conditions.reliability_data import freshness
from research.observing_conditions.features import utc
from research.observing_conditions.information_regions import representatives
from challenge.v4_workflow import V4Workflow
out=AGENT/'results/selected-models';out.mkdir(exist_ok=True)
requests=[]
for card in 'abcd':
    directory=ROOT/f'artifacts/observing_conditions-reliability/scenarios/reliable-{card}-0'
    init=V4Workflow(directory/'bundle').initialize_payload(900.,1.)
    init={k:init[k] for k in ('site','scoring','survey','instrument')}
    decisions=json.loads((directory/'decisions.json').read_text())
    by_phase={}
    for d in decisions:
        f=freshness(d['now_utc'],d['feedback_history'],init['survey']['nights'])
        phase='never' if not f['all_history_count'] else 'stale' if not f['fresh_history_count'] else 'partial' if f['fresh_history_count']<8 else 'eight'
        by_phase.setdefault(phase,d)
    for phase,d in by_phase.items():
        now=utc(d['now_utc']);candidates=[]
        starts=[now+timedelta(seconds=s) for s in (900,1800,3600)]
        nextnight=next((utc(n['observing_start_utc']) for n in init['survey']['nights'] if utc(n['observing_start_utc'])>now),None)
        if nextnight:starts.append(nextnight)
        for i,start in enumerate(starts):
            _,coordinates,_,_=representatives(init['site'],start.isoformat())
            for j,(ra,dec) in enumerate(coordinates):
                candidates.append(dict(candidate_id=str(j),ra_deg=float(ra),dec_deg=float(dec),start_utc=start.isoformat(),duration_seconds=(60,900,3600,900)[i]))
        bulletins=[json.loads(l) for l in (directory/'bundle/public/v4_bulletins.jsonl').read_text().splitlines() if l.strip()]
        forecasts=[json.loads(l) for l in (directory/'bundle/public/v4_forecasts.jsonl').read_text().splitlines() if l.strip()]
        requests.append(dict(initialize=init,now_utc=d['now_utc'],baseline_state=d['baseline_state'],feedback_history=d['feedback_history'],bulletins=bulletins,forecasts=forecasts,candidates=candidates))
path=out/'requests.jsonl';path.write_text(''.join(json.dumps(r,separators=(',',':'))+'\n' for r in requests))
import argparse,shutil
parser=argparse.ArgumentParser();parser.add_argument('--node',default=shutil.which('node'));args=parser.parse_args();node=args.node
env=dict(os.environ,BASE_MODEL_PYTHON=str(ROOT/'research/observing_conditions/.venv/bin/python'),OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
run=subprocess.run([node,str(AGENT/'dist/tools/predict-models.js')],input=path.read_text(),text=True,capture_output=True,env=env)
if run.returncode:
    print(run.stderr,file=sys.stderr);raise SystemExit(run.returncode)
(out/'typescript.jsonl').write_text(run.stdout)
actual=[json.loads(l) for l in run.stdout.splitlines()];reference=ReliabilityPredictor(ROOT/'artifacts/observing_conditions-reliability/selected')
checked=eligible=0;maximum=0;metadata_differences=[]
def compare(a,b,route):
    global checked,maximum
    if isinstance(a,dict):
        assert set(a)==set(b) or route==''
        for k in a:
            if k in ('timing','model_id'):continue
            compare(a[k],b[k],route+'/'+k)
    elif isinstance(a,list):
        assert len(a)==len(b)
        for i,(x,y) in enumerate(zip(a,b)):compare(x,y,route+'/'+str(i))
    elif isinstance(a,float):
        checked+=1;maximum=max(maximum,abs(a-b));assert abs(a-b)<=1e-6,(route,a,b)
    else:assert a==b,(route,a,b)
for request,response in zip(requests,actual):
    expect=reference.predict(request);compare(response,expect,'');eligible+=sum(r['eligible'] for r in response['predictions'])
print('conditions',len(requests),eligible,checked,maximum,flush=True)
# Actual portable inference implementation vs the trained classifier, including missing features.
import joblib
original=joblib.load(ROOT/'artifacts/instrument_faults-report-model/model.joblib')
spec=importlib.util.spec_from_file_location('selected_worker',AGENT/'runtime/worker.py');module=importlib.util.module_from_spec(spec)
saved=sys.stdin;sys.stdin=io.StringIO('')
try:spec.loader.exec_module(module)
finally:sys.stdin=saved
portable=module.PortableFault(AGENT/'models/instrument-fault-report')
xs=[]
for file in sorted((ROOT/'artifacts/instrument_faults-report-model/inputs').glob('*/data.npz'))[::5]:
    with np.load(file) as a:xs.append(a['x'][::max(1,len(a['x'])//120)])
x=np.concatenate(xs);p=portable.raw(x);expected=original.raw(x);error=float(np.max(np.abs(p-expected)));assert error<1e-12
print('fault',len(x),error,flush=True)
summary=dict(source='Formal public A-D and existing explicitly SELF-GENERATED reliability scenarios; no official hidden inputs',conditions=dict(model_version=actual[0]['model_version'],requests=len(requests),candidates=sum(len(r['candidates']) for r in requests),eligible=eligible,numeric_fields=checked,max_abs_error=maximum,all_metadata_equal=True,history_phases=sorted({r['public_history_phase'] for result in actual for r in result['predictions'] if r['eligible']})),fault=dict(model_version=portable.version,rows=len(x),features=x.shape[1],max_abs_error=error,score_is_calibrated_fault_probability=False))
(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
