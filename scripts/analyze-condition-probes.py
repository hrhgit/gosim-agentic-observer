#!/usr/bin/env python3
"""Estimate combined conditions from probe observations, correcting public geometry."""
import argparse
import bisect
import collections
import csv
from datetime import datetime
import importlib.util
import io
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agents/condition-probe-v1/project'))
from astro import Site,parse_utc,unit_vector
spec=importlib.util.spec_from_file_location('extract_capture',ROOT/'agents/claude-baseline-v1/tools/extract_capture.py')
extractor=importlib.util.module_from_spec(spec);spec.loader.exec_module(extractor)


def analyze(run, targets_file):
    decoded=io.StringIO()
    extractor.extract(io.StringIO((run/'agent.log').read_text()),decoded)
    captured=[json.loads(line) for line in decoded.getvalue().splitlines()]
    init=captured[0]['message']['payload'];scoring=init['scoring'];site_cfg=init['site']
    site=Site(site_cfg['latitude_deg'],site_cfg['longitude_deg'],site_cfg['minimum_altitude_deg'],scoring['airmass_exponent'],scoring.get('lunar_model',{}))
    with targets_file.open() as stream:target_rows=list(csv.DictReader(stream))
    columns=init['targets']['columns']
    numeric={'ra_deg','dec_deg','feature_flux','science_weight'}
    canonical_rows=[[float(t[c]) if c in numeric else t[c].lower()=='true' if c=='required' else t[c] for c in columns] for t in target_rows]
    fingerprint=hashlib.sha256(json.dumps(canonical_rows,ensure_ascii=True,separators=(',',':')).encode('ascii')).hexdigest()
    expected=captured[0]['omitted']['payload.targets.rows']
    if fingerprint!=expected['sha256'] or len(target_rows)!=expected['row_count']:
        raise ValueError('local target table does not match captured official input')
    targets={t['target_id']:t for t in target_rows}
    notes={}
    for record in captured:
        message=record['message']
        note=record.get('annotations')
        if message.get('message_type')=='decision_request' and note is not None and note.get('coverage')=='sampled':
            notes[int(message['payload']['observe_action_index'])]=note
    with (run/'decisions.csv').open() as stream:decisions={int(d['observe_index']):d for d in csv.DictReader(stream) if d['action']=='observe'}
    with (run/'observations.csv').open() as stream:observations=list(csv.DictReader(stream))
    values=[];per_observe=collections.defaultdict(list)
    for observation in observations:
        index=int(observation['observe_index']);note=notes.get(index)
        if note is None:continue
        decision=decisions[index];target=targets[observation['target_id']]
        vector=unit_vector(float(target['ra_deg']),float(target['dec_deg']))
        start=parse_utc(decision['start_utc']);end=parse_utc(decision['end_utc'])
        # Probe exposures are 60 s: the official scorer uses one midpoint piece.
        # For changed longer exposures use the same <=120 s piece midpoints.
        duration=end-start;weighted=0.0;weight=0.0;cursor=start
        while cursor<end:
            piece_end=min(cursor+120,end);middle=(cursor+piece_end)/2
            _,zenith,_,_=site.frame(middle)
            sine=sum(a*b for a,b in zip(vector,zenith))
            geometry=site.transmission(sine)*site.lunar_factor(*vector,site.moon(middle))
            weighted+=(piece_end-cursor)*geometry;weight+=piece_end-cursor;cursor=piece_end
        geometry=weighted/weight
        quality=float(observation['quality'])
        combined=quality*float(scoring['q0'])/geometry if geometry>0 else None
        row={'observe_index':index,'pair_id':note['pair_id'],'role':note['role'],'target_id':target['target_id'],'start_utc':decision['start_utc'],'alt_deg':note['actual_alt_deg'],'az_deg':note['actual_az_deg'],'quality':quality,'geometry_factor':geometry,'combined_condition':combined,'valid':observation['valid']}
        values.append(row)
        if observation['valid']=='true' and combined is not None:per_observe[index].append(combined)
    night_starts=[parse_utc(n['observing_start_utc']) for n in init['survey']['nights']]
    nightly=collections.defaultdict(list)
    for index, samples in per_observe.items():
        moment=parse_utc(decisions[index]['start_utc'])
        night_index=bisect.bisect_right(night_starts,moment)-1
        night_id=init['survey']['nights'][night_index]['night_id']
        note=notes[index]
        direction_bin=int((note['actual_az_deg']+22.5)//45)%8
        key=(night_id,direction_bin)
        nightly[key].append(statistics.median(samples))
    with (run/'probe-condition-by-night.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['night_id','direction','measured_exposures','median_combined_condition','min_combined_condition','max_combined_condition'])
        writer.writeheader()
        for (night_id,direction_bin),samples in sorted(nightly.items()):
            writer.writerow({'night_id':night_id,'direction':('N','NE','E','SE','S','SW','W','NW')[direction_bin],'measured_exposures':len(samples),'median_combined_condition':statistics.median(samples),'min_combined_condition':min(samples),'max_combined_condition':max(samples)})
    groups=collections.defaultdict(dict)
    for index,note in notes.items():
        if index not in per_observe:continue
        groups[note['pair_id']][note['role']]={'observe_index':index,'combined':statistics.median(per_observe[index]),'sample_targets':len(per_observe[index]),'time':decisions[index]['start_utc'],'alt':note['actual_alt_deg'],'az':note['actual_az_deg']}
    pairs=[]
    for pair_id,group in groups.items():
        if 'affected_or_sweep' not in group or 'reference' not in group:continue
        first,second=group['affected_or_sweep'],group['reference']
        gap=parse_utc(second['time'])-parse_utc(first['time']);alt_gap=abs(second['alt']-first['alt'])
        eligible=0<=gap<=300 and alt_gap<=5 and second['combined']>0
        pairs.append({'pair_id':pair_id,'first':first,'reference_candidate':second,'gap_seconds':gap,'altitude_difference_deg':alt_gap,'eligible_for_short_gap_ratio':eligible,'quality_ratio':first['combined']/second['combined'] if eligible else None,'interpretation':'reference is not guaranteed unaffected; this ratio is not an isolated event severity'})
    if values:
        with (run/'probe-measurements.csv').open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(values[0]));writer.writeheader();writer.writerows(values)
    (run/'probe-pairs.json').write_text(json.dumps(pairs,ensure_ascii=False,indent=2)+'\n')
    report=json.loads((run/'score_report.json').read_text())
    summary={'termination':report['termination'],'observed_exposures':report['counts']['observe_actions'],'measured_target_hits':len(values),'pairs_with_both_samples':len(pairs),'short_gap_comparable_pairs':sum(p['eligible_for_short_gap_ratio'] for p in pairs),'logged_bytes':(run/'agent.log').stat().st_size,'measurement':'effective efficiency * transparency * sky_quality / seeing, after known lunar/airmass correction; approximate if weather changes within exposure','unknown':'cannot uniquely separate components; missing hits may reflect pointing/fiber geometry, not weather; zero means closure or zero-quality, not a unique cause','invalidations':'invalidated hits retained but excluded from paired estimates','full_survey_completed':report['termination']['reason']=='survey_complete'}
    (run/'probe-analysis.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--targets',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(analyze(args.run,args.targets),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
