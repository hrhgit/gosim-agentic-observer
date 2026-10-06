#!/usr/bin/env python3
"""Compare assumed weather to held recorded target exposures; no parameter fitting."""
import argparse
import csv
from datetime import datetime
import io
import json
from pathlib import Path
import statistics
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from challenge.v4_workflow import V4Workflow
from challenge.v4_scorer import WeatherTruth,score_target_exposure
from challenge.v4_fiber_map import radec_to_altaz


def dt(value):return datetime.fromisoformat(value.replace('Z','+00:00'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite',type=Path,default=ROOT/'evaluation_assets/formal-v4-observed-timeline-v4')
    parser.add_argument('--archive',type=Path,default=ROOT/'artifacts/formal-feedback-2026-10-05/selective-capture-v2-evaluation.zip')
    parser.add_argument('--samples',type=int,default=256)
    parser.add_argument('--holdout-odd',action='store_true',help='only odd observe_index; reserved when new calibration fits even exposures')
    args=parser.parse_args()
    if args.samples<2:parser.error('samples must be at least 2')
    summary={}
    with zipfile.ZipFile(args.archive) as archive:
        for card in 'abcd':
            folder=next(name.rsplit('/',1)[0] for name in archive.namelist() if name.endswith('/score_report.json') and json.loads(archive.read(name))['scenario'] in (f'v4-{card}',f'v4-{card}1-v5'))
            observations=list(csv.DictReader(io.StringIO(archive.read(folder+'/observations.csv').decode())))
            valid=[row for row in observations if row['valid']=='true' and (not args.holdout_odd or int(row['observe_index'])%2==1)]
            selected=[valid[i*(len(valid)-1)//(min(args.samples,len(valid))-1)] for i in range(min(args.samples,len(valid)))]
            decisions={int(row['observe_index']):row for row in csv.DictReader(io.StringIO(archive.read(folder+'/decisions.csv').decode())) if row['action']=='observe'}
            scenario=V4Workflow(args.suite/'cards'/card).scenario
            weather=WeatherTruth(scenario.slots,[e for e in scenario.events if e.event_type=='instrument_fault'])
            messages=[json.loads(line) for line in archive.read(folder+'/messages.jsonl').decode().splitlines()]
            for msg in messages:
                if msg['record_type']=='report_result' and msg['correct']:
                    assert weather.repair_fault(dt(msg['issued_at_utc']))
            targets={t['target_id']:t for t in scenario.targets}
            site=scenario.config['site'];rows=[]
            for actual in selected:
                decision=decisions[int(actual['observe_index'])];target=targets[actual['target_id']]
                start,end=dt(decision['start_utc']),dt(decision['end_utc'])
                altaz=lambda moment:radec_to_altaz(target['ra_deg'],target['dec_deg'],moment,site['latitude_deg'],site['longitude_deg'])
                directional=[e for e in scenario.events if e.scope_type=='HORIZON_SECTOR' and e.overlaps(start,end)]
                predicted=score_target_exposure(target,list(weather.segments(start,end)),directional,weather,int((end-start).total_seconds()),float(actual['prog_mult']),scenario.score_config,start,altaz,site)
                rows.append({'observe_index':actual['observe_index'],'target_id':actual['target_id'],'start_utc':decision['start_utc'],'online_quality':float(actual['quality']),'assumed_quality':predicted.quality,'absolute_error':abs(predicted.quality-float(actual['quality']))})
            relative=[row['absolute_error']/row['online_quality'] for row in rows if row['online_quality']>0]
            summary[card]={'sample_targets':len(rows),'selection':'equally spaced valid observation rows; not independent events or sky coverage','mean_absolute_quality_error':statistics.mean(row['absolute_error'] for row in rows),'median_relative_quality_error_nonzero':statistics.median(relative),'within_10_percent_nonzero_fraction':sum(v<=.1 for v in relative)/len(relative),'zero_quality_agreement_fraction':sum((row['online_quality']==0)==(row['assumed_quality']==0) for row in rows)/len(rows),'comparison_boundary':'recorded hits/exposure times and program multiplier; does not validate hit geometry, program choice, requests, or final score','fitted_on_sample':False,'odd_exposures_only':args.holdout_odd,'evaluation_scope':'reserved exposures of the same recorded weather, not independent scenarios'}
            with (args.suite/'cards'/card/'generator/quality-comparison-sample.csv').open('w',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    (args.suite/'weather-comparison.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
