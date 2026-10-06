"""Reproducible scene preparation; training and final-test stages are separate."""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import time
from .adaptation_data import DEFAULT, entries, protocol, build_samples, build_online_records
from .adaptation_rollout import run
from .data import save_json


def prepare_one(entry, output):
    started=time.perf_counter()
    trajectory=run(entry,Path(output)/'scenarios'/entry['id'],official_audit=entry['id']=='adapt-a-0')
    samples=build_samples(entry,output)
    online=build_online_records(entry,output)
    return dict(entry=entry,trajectory=trajectory,samples=samples,online=online,
                elapsed_seconds=time.perf_counter()-started)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=DEFAULT)
    parser.add_argument('--stage',choices=('development','test'),default='development')
    parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args();protocol(args.output)
    if args.stage=='test' and not (args.output/'frozen.json').exists():
        raise ValueError('Freeze selection, update rule and calibration before generating test labels')
    chosen=[e for e in entries() if (e['split']=='test')==(args.stage=='test')]
    started=time.perf_counter();results=[]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(prepare_one,e,args.output):e for e in chosen}
        for f in as_completed(futures):
            r=f.result();results.append(r)
            save_json(args.output/('preparation_'+args.stage+'.json'),dict(results=results,
                elapsed_seconds=time.perf_counter()-started,workers=args.workers))
            print(r['entry']['id'],r['trajectory']['exposures'],r['samples']['records'],
                  r['online']['clean_exposures'],round(r['elapsed_seconds'],2),flush=True)


if __name__=='__main__':main()
