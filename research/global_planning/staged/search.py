"""Public-only sampled score dynamics. Never accepts a scenario or truth directory.

This deliberately approximate model is audited on public feedback before search
is considered for adoption. Search traces expose its uncertainty and limitations.
"""
from __future__ import annotations
import copy, gzip, hashlib, json, math
from datetime import datetime,timedelta
from pathlib import Path
import numpy as np
import torch
from ..common import save
from .training import CONFIG,Policy
from .features import encode

def public_pairs(trajectories):
    rows=[]
    for trajectory in trajectories:
        weight=1.;before=0.;proposal=None
        with gzip.open(trajectory.folder/'trace.jsonl.gz','rt') as stream:
            for line in stream:
                event=json.loads(line)
                if event['type']=='initialize':
                    table=event['payload']['targets'];column=table['columns'].index('science_weight')
                    weight=sum(float(r[column]) for r in table['rows'])
                elif event['type']=='proposal':
                    f=event['frame'];proposal=f['candidates'][event['selected']]['features']
                elif event['type']=='observation':
                    total=event['payload']['running_total']
                    if proposal is not None:
                        expected=proposal['expected_science']*weight
                        if expected>1e-6:rows.append(dict(predicted=expected,actual=total-before))
                        proposal=None
                    before=total
    return rows

class FutureSampler:
    def __init__(self,state):self.state=state
    @classmethod
    def fit(cls,trajectories):
        rows=public_pairs(trajectories)
        ratios=np.array([max(0,r['actual'])/r['predicted'] for r in rows])
        if len(ratios)<20:raise ValueError('Insufficient public evidence for future sampler')
        return cls(dict(version='public-return-ratio-v1',source='TRAIN public snapshots and candidate estimates only',
            ratio_quantiles=np.quantile(ratios,np.linspace(0,1,101)).tolist(),mean=float(ratios.mean()),
            interval_90=np.quantile(ratios,[.05,.95]).tolist(),samples=len(rows),
            limitations=['No calibrated full weather/event sequence','Nearby decision residuals use independent draws',
                         'Future placements reuse current legal options; future visibility is approximate']))
    def audit(self,trajectories):
        rows=public_pairs(trajectories);lo,hi=self.state['interval_90']
        ratios=[max(0,r['actual'])/r['predicted'] for r in rows]
        coverage=float(np.mean([lo<=v<=hi for v in ratios])) if ratios else 0.
        # Calibration is necessary, but cannot establish event-sequence correctness.
        return dict(samples=len(rows),coverage_90=coverage,coverage_gate=abs(coverage-.9)<=.1,
            future_process_validated=False,adoption_ready=False,
            reason='One-step score residual coverage cannot validate a full correlated weather/event process',
            source='VALIDATION public feedback only')
    def sample(self,rng):return float(np.interp(rng.random(),np.linspace(0,1,101),self.state['ratio_quantiles']))

class SearchPolicy:
    def __init__(self,policy,sampler,expansions=16,depth=2):
        if (expansions,depth) not in [(16,2),(64,3)]:raise ValueError('Unregistered search setting')
        self.policy,self.sampler,self.expansions,self.depth=policy,sampler,expansions,depth
    def __call__(self,frame):
        logits,value,memory=self.policy.predict(frame);self.policy.memory=memory
        direct=int(logits.argmax())
        if frame['kind'] not in ('region','duration'):return direct,dict(trainable=False,value=float(value)*CONFIG['return_scale'])
        # Seed solely from complete public input; no scene ID, seed, or real future.
        x,c=encode(frame)
        seed=int.from_bytes(hashlib.sha256(x.tobytes()+c.tobytes()).digest()[:8],'little')
        rng=np.random.default_rng(seed);probs=logits.softmax(-1).numpy();scores={};counts={};nodes=0
        while nodes<self.expansions:
            root=direct if nodes==0 else int(rng.choice(len(probs),p=probs))
            simulated=copy.deepcopy(frame);local_memory=memory.clone();reward=0.;index=root
            for level in range(self.depth):
                if nodes>=self.expansions:break
                candidate=simulated['candidates'][index];f=candidate['features'];a=candidate['action']
                factor=self.sampler.sample(rng);gain=0. if a['action']=='wait' else f['expected_science']*factor
                # Science increments are normalized by total catalogue weight in frames.
                # Pending terminal bonuses remain with the value head; no invented reward.
                reward+=gain
                simulated['global']['science_fraction']+=gain
                duration=float(a.get('duration_seconds',0));g=simulated['global']
                old_tonight=g['tonight_hours_left'];g['tonight_hours_left']=max(0,old_tonight-duration/3600)
                g['total_time_fraction_left']*=max(0,1-duration/max(1,old_tonight*3600/max(g['total_time_fraction_left'],1e-9)))
                moment=datetime.fromisoformat(simulated['now_utc'].replace('Z','+00:00'))+timedelta(seconds=duration)
                simulated['now_utc']=moment.isoformat().replace('+00:00','Z')
                # Remove completed scientific potential from revisiting this same arrangement.
                for target in f['targets']:target['best']=max(target['best'],target['science_weight']*min(1,target['factor_estimate']*factor))
                f['expected_science']=max(0,f['expected_science']-gain)
                future_logits,endpoint,local_memory=self.policy.predict(simulated,local_memory);nodes+=1
                index=int(rng.choice(len(future_logits),p=future_logits.softmax(-1).numpy()))
            # Convert public normalized science to score units using an explicit public field.
            estimate=reward*frame['global']['catalogue_weight']+float(endpoint)*CONFIG['return_scale']
            scores[root]=scores.get(root,0.)+estimate;counts[root]=counts.get(root,0)+1
        selected=max(scores,key=lambda i:scores[i]/counts[i])
        return selected,dict(trainable=False,value=float(value)*CONFIG['return_scale'],search_nodes=nodes,
                             search_depth=self.depth,direct=direct,sampled_estimates={str(i):scores[i]/counts[i] for i in scores},
                             future_process_validated=False)
