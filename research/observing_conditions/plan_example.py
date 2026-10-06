"""Example complete plans: official A catalog, self-generated public notices."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from challenge.v4_fiber_map import FiberGrid,radec_to_altaz
from .data import ROOT,DEFAULT_OUTPUT,save_json
from .example import build_request
from .features import utc


def build():
    existing=build_request(DEFAULT_OUTPUT,'a-000',0)
    config=json.loads((ROOT/'cards/a/config/v4_fiber_config.json').read_text())
    rows=list(csv.DictReader((ROOT/'cards/a/public/targets.csv').open()))
    for row in rows:
        for k in ('ra_deg','dec_deg','feature_flux','science_weight'):row[k]=float(row[k])
        row['required']=row['required'].lower()=='true'
    columns=list(rows[0]);initialize=existing['initialize']
    grid=FiberGrid.from_config(config)
    initialize['instrument'].update(config['field']);initialize['instrument']['grid_side']=grid.n_side
    initialize['targets']=dict(columns=columns,rows=[[r[c] for c in columns] for r in rows])
    ra=np.radians([t['ra_deg'] for t in rows]);dec=np.radians([t['dec_deg'] for t in rows])
    vectors=np.column_stack([np.cos(dec)*np.cos(ra),np.cos(dec)*np.sin(ra),np.sin(dec)])
    plans=[]
    for index,c in enumerate(existing['candidates'][:24]):
        start=utc(c['start_utc']);site=initialize['site']
        alt,az=radec_to_altaz(c['ra_deg'],c['dec_deg'],start,site['latitude_deg'],site['longitude_deg'])
        a,d=np.radians([c['ra_deg'],c['dec_deg']])
        center=np.array([np.cos(d)*np.cos(a),np.cos(d)*np.sin(a),np.sin(d)])
        radius=np.degrees(np.arctan(np.sqrt(2)*np.radians(grid.fov_side_deg/2)))+.01
        ix=np.flatnonzero(vectors@center>=np.cos(np.radians(radius)))
        assignments={}
        for i in ix:
            target=rows[int(i)]
            hit=grid.classify_target(target['ra_deg'],target['dec_deg'],start,alt,az,
                site['latitude_deg'],site['longitude_deg'])
            if hit.region=='glass' and str(hit.fiber_id) not in assignments:
                assignments[str(hit.fiber_id)]=target['target_id']
        plans.append(dict(plan_id=f'example-plan-{index}',pointing=dict(alt_deg=alt,az_deg=az),
            assignments=assignments,start_utc=c['start_utc'],duration_seconds=c['duration_seconds'],program='DARK'))
    # Demo selection uses public occupancy only, never weather truth or scores.
    selected=sorted(plans,key=lambda p:len(p['assignments']),reverse=True)[:2]
    return {k:v for k,v in existing.items() if k!='candidates'} | dict(observation_plans=selected)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'artifacts/observing_conditions-plans')
    args=parser.parse_args();save_json(args.output/'example_request.json',build())
    save_json(args.output/'source.json',dict(source='Official A public catalog and instrument; notices from self-generated a-000 scenario',
        plans='Two demo pointings chosen by nominal public fiber occupancy; NOT agent strategies or official scores'))
    print(args.output/'example_request.json')
