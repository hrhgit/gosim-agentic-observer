"""Optional second local process: overlap the TCN GPU work with CPU architectures.

Uses exactly the same jobs, locks, seeds and checkpoints as the main experiment.
It never reads calibration or test data, and does not select an architecture.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np

from .data import DEFAULT_OUTPUT,load_dataset
from .models import Normalizer
from .experiment import train_job,subset,ablate,SEEDS


def transfer_jobs(output,architecture,train,validation,identity,device):
    """Prefetch identical transfer jobs while the main process waits for another architecture."""
    comparison=json.loads((output/'validation_comparison.json').read_text())
    if architecture not in comparison:
        raise ValueError('complete the architecture comparison before prefetching its transfer jobs')
    lr=comparison[architecture]['learning_rate']
    for name in [*'abcd','no_history','no_forecast']:
        selection=output/'selection.json'
        if selection.exists() and json.loads(selection.read_text())['architecture']!=architecture:
            print('PREFETCH STOP: validation selected another architecture',flush=True)
            return
        if name in 'abcd':
            limited=subset(train,train['card']!=name)
            limited_val=subset(validation,validation['card']!=name)
            path=output/'models'/f'leave-{name}-{architecture}'
            job_identity=identity+'-leave-'+name
        else:
            limited=ablate(train,name);limited_val=ablate(validation,name)
            path=output/'models'/f'{name}-{architecture}'
            job_identity=identity+'-'+name
        normalizer=Normalizer.fit(limited)
        train_job(path,architecture,limited,limited_val,normalizer,lr,60,101,device,job_identity)
    print('TRANSFER PREFETCH COMPLETE',flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    parser.add_argument('--architecture',choices=('mlp','gru','tcn'),default='tcn')
    parser.add_argument('--transfer',action='store_true')
    args=parser.parse_args();output=args.output
    if not (output/'audit.json').exists() or not (output/'device_benchmarks.json').exists():
        raise ValueError('run generation/audit and local device benchmarks first')
    identity=hashlib.sha256((output/'manifest.json').read_bytes()).hexdigest()
    train=load_dataset(output,['train']);validation=load_dataset(output,['validation'])
    normalizer=Normalizer.fit(train)
    architecture=args.architecture
    device=json.loads((output/'device_benchmarks.json').read_text())[architecture]['selected']
    if args.transfer:
        transfer_jobs(output,architecture,train,validation,identity,device)
        return
    tuning=subset(train,np.array([i%128<8 for i in range(len(train['context']))]))
    choices=[]
    for lr in (3e-4,1e-3):
        path=output/'models'/f'tune-{architecture}-{lr}'
        train_job(path,architecture,tuning,validation,normalizer,lr,20,404,device,identity)
        choices.append((json.loads((path/'complete.json').read_text())['best_validation_score'],lr))
    lr=min(choices)[1]
    for seed in SEEDS:
        train_job(output/'models'/f'{architecture}-{seed}',architecture,train,validation,normalizer,lr,60,seed,device,identity)
    print(architecture.upper()+' PRETRAIN COMPLETE',flush=True)


if __name__=='__main__':
    main()
