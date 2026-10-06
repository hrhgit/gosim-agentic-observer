"""Condition predictions for complete proposed observations, without selection.

Uses the actual initialize target table and instrument layout. A plan is one
pointing plus up to n_fibers assigned targets; all submitted plans are returned.
Quality remains per target. No joint probability or expected capped score is
invented from marginal quality predictions.
"""
from __future__ import annotations

from .tree_runtime import launch
if __name__=='__main__':launch('research.observing_conditions.plan_predict')

import argparse
import json
import math
from pathlib import Path
import time

from challenge.v4_fiber_map import FiberGrid
from .data import DEFAULT_OUTPUT,save_json
from .features import utc
from .predict import Predictor


def target_table(payload):
    columns=payload['columns'];rows=payload['rows']
    allowed={'target_id','ra_deg','dec_deg','target_class','feature_flux','science_weight','required'}
    if set(columns)!=allowed or len(columns)!=len(allowed):
        raise ValueError('expected official public target columns')
    result={}
    for row in rows:
        if len(row)!=len(columns):raise ValueError('malformed target row')
        t=dict(zip(columns,row));tid=str(t['target_id'])
        if tid in result:raise ValueError('duplicate target ID')
        for name in ('ra_deg','dec_deg','feature_flux','science_weight'):
            t[name]=float(t[name])
            if not math.isfinite(t[name]):raise ValueError('nonfinite public target value')
        if not 0<=t['ra_deg']<360 or not -90<=t['dec_deg']<=90:
            raise ValueError('invalid public target coordinates')
        if t['feature_flux']<0 or t['science_weight']<0:raise ValueError('negative target value')
        if isinstance(t['required'],str) and t['required'].lower() in ('true','false'):
            t['required']=t['required'].lower()=='true'
        if not isinstance(t['required'],bool):raise ValueError('invalid required flag')
        t['target_id']=tid;result[tid]=t
    return result


class PlanPredictor:
    def __init__(self,predictor):
        self.predictor=predictor

    def predict(self,request):
        begin=time.perf_counter()
        allowed={'initialize','now_utc','bulletins','forecasts','observation_plans'}
        if set(request)-allowed:raise ValueError('unsupported plan request fields')
        initialize=request['initialize'];instrument=initialize['instrument'];site=initialize['site']
        targets=target_table(initialize['targets'])
        grid=FiberGrid(instrument['fiber_area_deg2'],instrument['gap_deg'],instrument['n_fibers'])
        flat=[];plans=[];ids=set()
        for pi,plan in enumerate(request['observation_plans']):
            if set(plan)-{'plan_id','pointing','assignments','start_utc','duration_seconds','program'}:
                raise ValueError('unsupported observation plan fields')
            pid=str(plan['plan_id'])
            if pid in ids:raise ValueError('duplicate plan ID')
            ids.add(pid)
            if set(plan['pointing'])!={'alt_deg','az_deg'}:raise ValueError('unsupported pointing fields')
            alt=float(plan['pointing']['alt_deg']);az=float(plan['pointing']['az_deg'])
            if not math.isfinite(alt+az) or not 0<=alt<=90 or not 0<=az<360:
                raise ValueError('invalid commanded pointing')
            if plan.get('program') not in (None,'DARK','BRIGHT','BACKUP'):
                raise ValueError('invalid declared program')
            start=utc(plan['start_utc']);duration=plan['duration_seconds']
            exposure=instrument['exposure']
            if isinstance(duration,bool) or not isinstance(duration,int) or not exposure['min_duration_seconds']<=duration<=exposure['max_duration_seconds']:
                raise ValueError('requested exposure outside public limits')
            if (start-utc(request['now_utc'])).total_seconds() not in (0,900,1800,3600):
                raise ValueError('supported starts: now, +15, +30, +60 minutes')
            assignments=plan['assignments']
            if not isinstance(assignments,dict) or len(assignments)>grid.n_fibers:
                raise ValueError('too many or malformed fiber assignments')
            used_fibers=set();used_targets=set();rows=[]
            for key,target_id in assignments.items():
                if isinstance(key,bool) or not (isinstance(key,int) or isinstance(key,str) and key.isdigit()):
                    raise ValueError('invalid fiber ID')
                fid=int(key);tid=str(target_id)
                if not 0<=fid<grid.n_fibers or fid in used_fibers:raise ValueError('invalid or duplicate fiber ID')
                if tid in used_targets:raise ValueError('target assigned more than once')
                if tid not in targets:raise ValueError('unknown public target')
                used_fibers.add(fid);used_targets.add(tid);t=targets[tid]
                hit=grid.classify_target(t['ra_deg'],t['dec_deg'],start,alt,az,
                    site['latitude_deg'],site['longitude_deg'])
                rows.append(dict(target=t,fiber_id=fid,prediction_index=len(flat),
                    nominal_fiber_match=hit.region=='glass' and hit.fiber_id==fid,
                    nominal_actual_fiber_id=hit.fiber_id))
                flat.append(dict(candidate_id=f'plan-{pi}-fiber-{fid}',ra_deg=t['ra_deg'],dec_deg=t['dec_deg'],
                    start_utc=plan['start_utc'],duration_seconds=duration))
            plans.append((plan,rows))
        raw=self.predictor.predict({k:request[k] for k in allowed-{'observation_plans'} if k in request} | dict(candidates=flat))
        result={k:raw[k] for k in ('model_version','architecture','quality_definition','interval_coverage_target','training_source')}
        result.update(schema_version='public-observation-plans-v1',max_targets_per_observation=grid.n_fibers,
            submitted_plan_count=len(plans),returned_plan_count=len(plans),automatic_actions_enabled=False,
            weather_exclusion_enabled=False,fixed_top_k=None,
            numerical_final_test_passed=raw.get('final_test_passed'),numerical_acceptance=raw.get('acceptance'),
            interval_method=raw.get('interval_method','trained quality quantiles plus held-out calibration'),
            median_method=raw.get('median_method','trained conditional median estimate'),
            source_model_assistance_acceptance=raw.get('assistance_acceptance'),
            instrument_assumption='efficiency=1; zero pointing offset; hidden faults excluded',
            aggregate_interval=None,joint_success_probability=None,
            summary_method='marginal target predictions; sum of marginal usable probabilities; no independence assumption',
            factor_method='official capped formula evaluated at predicted Q; per-target potential conditional on a valid fiber hit; '
                'summary sums nominally matched targets only; plug-in estimate, NOT expected score, '
                'NOT guaranteed completion or actual incremental competition score',
            plans=[])
        score=initialize['scoring']
        denominator=score['flux_zero_point']*score['exposure_zero_point_seconds']
        threshold=score['required']['observed_factor_threshold']
        for plan,rows in plans:
            out=[]
            for row in rows:
                t=row['target'];prediction=raw['predictions'][row['prediction_index']]
                item={k:prediction[k] for k in ('eligible','reason','actual_duration_seconds','known_geometry_factor',
                    'mean_quality','q05','q50','q95','usable_fraction','severe_block_probability',
                    'p_dark','p_bright','p_backup') if k in prediction}
                item.update(target_id=t['target_id'],fiber_id=row['fiber_id'],required=t['required'],
                    science_weight=t['science_weight'],nominal_fiber_match=row['nominal_fiber_match'],
                    nominal_actual_fiber_id=row['nominal_actual_fiber_id'],
                    known_unavailable=not prediction['eligible'],suggest_weather_exclusion=False)
                if prediction['eligible']:
                    scale=t['feature_flux']*prediction['actual_duration_seconds']/denominator
                    factors=[min(1.,max(0.,prediction[k]*scale)) for k in ('mean_quality','q05','q95')]
                    item.update(raw_factor_at_mean_quality=factors[0],factor_interval=[factors[1],factors[2]],
                        base_score_at_mean_quality=t['science_weight']*factors[0],
                        required_factor_threshold=threshold,
                        predicted_interval_vs_required_threshold=('below' if factors[2]<threshold else
                            'above_or_equal' if factors[1]>=threshold else 'overlaps'))
                    if plan.get('program'):
                        item['declared_program_match_probability']=prediction['p_'+plan['program'].lower()]
                out.append(item)
            contributing=[t for t in out if t['eligible'] and t['nominal_fiber_match']]
            result['plans'].append(dict(plan_id=str(plan['plan_id']),pointing=plan['pointing'],start_utc=plan['start_utc'],
                duration_seconds=plan['duration_seconds'],declared_program=plan.get('program'),targets=out,
                summary=dict(assigned_targets=len(out),nominally_scoring_targets=len(contributing),
                    required_targets=sum(t['required'] for t in out),known_unavailable_targets=sum(t['known_unavailable'] for t in out),
                    nominal_assignment_mismatches=sum(not t['nominal_fiber_match'] for t in out),
                    estimated_targets_with_at_least_half_usable_time=sum(1-t['severe_block_probability'] for t in contributing),
                    mean_predicted_quality=sum(t['mean_quality'] for t in contributing)/len(contributing) if contributing else None,
                    base_score_at_mean_quality=sum(t['base_score_at_mean_quality'] for t in contributing),
                    weather_excluded_targets=0)))
        result['timing']=dict(target_prediction=raw['timing'],total_seconds=time.perf_counter()-begin)
        return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--result',type=Path,required=True);parser.add_argument('--backend',choices=('neural','trees'),default='neural')
    parser.add_argument('--model',type=Path)
    args=parser.parse_args()
    if args.backend=='trees':
        from .tree_predict import TreePredictor
        predictor=TreePredictor(args.model) if args.model else TreePredictor()
    else:predictor=Predictor(args.model or DEFAULT_OUTPUT/'models/selected')
    save_json(args.result,PlanPredictor(predictor).predict(json.loads(args.input.read_text())))


if __name__=='__main__':main()
