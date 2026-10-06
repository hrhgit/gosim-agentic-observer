"""Organizer-side checks against the real official scorer, never model inputs."""
from __future__ import annotations

import json
from pathlib import Path
from datetime import timedelta
from functools import lru_cache

import numpy as np

from challenge.v4_runner import load_scenario, run_scenario
from challenge.v4_fiber_map import radec_to_altaz
from challenge.v4_scorer import WeatherTruth, score_target_exposure, program_multiplier, program_band, lunar_quality_factor
from research.observing_conditions.features import utc
from research.observing_conditions.data import save_json
from .simulation import exposure_scores, fault_label, simulate


@lru_cache(maxsize=16)
def audit_assets(directory,track):
    directory=Path(directory)
    scenario=load_scenario(directory/'scenario.json')
    rows=json.loads((directory/f'schedule-{track}.json').read_text())
    with np.load(directory/f'physics-{track}.npz') as a:physics={k:a[k] for k in a.files}
    return scenario,rows,physics


class AlwaysHealthy:
    def predict_proba(self,x):
        return np.zeros(len(x))


def diagnostic_predictions(rows):
    """Explicit constant public placeholder ONLY for scorer harness checks."""
    n=max((len(r['target_indices']) for r in rows),default=0)
    return np.tile(np.array([.5,.2,.5,.8,1,0,1/3,1/3,1/3],np.float32),(len(rows),n,1))


def official_fixed_schedule(directory,track,summary,output):
    rows=json.loads((directory/f'schedule-{track}.json').read_text())
    reports=iter(summary['reports']); pending=next(reports,None)
    def factory(_):
        position=0
        def agent(observation):
            nonlocal position,pending
            now=utc(observation['now_utc'])
            if pending and utc(pending['issued_at_utc'])<=now:
                pending=next(reports,None)
                return {'action':'report'}
            next_start=utc(rows[position]['start']) if position<len(rows) else utc(observation['survey_end_utc'])
            if now<next_start:
                return {'action':'wait','until_utc':next_start.isoformat()}
            if position==len(rows):
                return {'action':'wait','until_utc':observation['survey_end_utc']}
            row=rows[position];position+=1
            return dict(action='observe',pointing={'alt_deg':row['altitude'],'az_deg':row['azimuth']},
                        assignments=row['assignments'],duration_seconds=row['requested'],program=row['program'])
        return agent
    return run_scenario(directory/'scenario.json',factory,output)


def audit(output,samples=200,runner=True):
    output=Path(output)
    manifest=json.loads((output/'manifest.json').read_text())
    entries=manifest['entries'];rng=np.random.default_rng(484021)
    errors=[];records=[];runs=[]
    for index in range(samples):
        entry=entries[index%len(entries)];directory=output/'scenarios'/entry['id'];track=index%2
        scenario,rows,physics=audit_assets(str(directory),track)
        row=rows[int(rng.integers(len(rows)))];start,end=utc(row['start']),utc(row['end'])
        faults=[e for e in scenario.events if e.event_type=='instrument_fault'];weather=WeatherTruth(scenario.slots,faults)
        repaired=bool(index%2)
        if repaired:
            for event in faults:
                if event.actual_start_utc<=start:
                    weather.repair_fault(max(event.actual_start_utc,start if event.overlaps(start,end) else event.actual_end_utc-timedelta(seconds=1)))
        expected=any(e.actual_start_utc<=end<e.actual_end_utc and e.event_id not in weather.fault_repairs for e in faults)
        if fault_label(weather,end)!=expected:
            raise AssertionError('label differs from official event/repair state')
        values,factors,quality,hits=exposure_scores(scenario,row,physics,weather)
        for ti,target_index in enumerate(row['target_indices'][:1]):
            target=scenario.targets[target_index]
            position=lambda at:radec_to_altaz(target['ra_deg'],target['dec_deg'],at,scenario.site['latitude_deg'],scenario.site['longitude_deg'])
            band=weather.band_quality(start,end,scenario.score_config,lambda at:position(at)[0],
                 lambda at:lunar_quality_factor(target['ra_deg'],target['dec_deg'],at,scenario.site,scenario.score_config))
            multiplier=program_multiplier(row['program'],program_band(band,scenario.score_config),scenario.score_config)
            direct=score_target_exposure(target,list(weather.segments(start,end)),
                   [e for e in scenario.events if e.scope_type=='HORIZON_SECTOR' and e.overlaps(start,end)],weather,
                   row['duration'],multiplier,scenario.score_config,start,position,scenario.site)
            delta=abs(direct.score-values[ti]);errors.append(delta)
            if delta>1e-5:
                raise AssertionError(f'official score mismatch {entry["id"]} {row["observe_index"]}: {delta}')
        records.append(dict(scene=entry['id'],track=track,observe_index=row['observe_index'],label=int(expected),repaired=repaired,
                            max_score_error=errors[-1] if errors else 0))
        if (index+1)%25==0:print(f'official sample checks {index+1}/{samples}',flush=True)
    save_json(output/'audit-samples.json',dict(samples=samples,label_errors=0,maximum_score_error=max(errors,default=0),records=records))
    if runner:
        # Each card runs through the actual runtime including D's resync and offset.
        for card in 'abcd':
            entry=next(e for e in entries if e['card']==card and e['fault_count'])
            directory=output/'scenarios'/entry['id'];track=1
            rows=json.loads((directory/f'schedule-{track}.json').read_text());p=diagnostic_predictions(rows)
            _,_,_,none=simulate(directory,track,p,'DIAGNOSTIC_CONSTANT_ONLY')
            _,_,_,healthy=simulate(directory,track,p,'DIAGNOSTIC_CONSTANT_ONLY','model',AlwaysHealthy())
            _,_,_,oracle=simulate(directory,track,p,'DIAGNOSTIC_CONSTANT_ONLY','oracle')
            if abs(healthy['total']-none['total'])>1e-9 or healthy['reports']:
                raise AssertionError('constant healthy model differs from no report')
            for kind,summary in [('none',none),('oracle',oracle)]:
                print(f'official full runtime {entry["id"]}/{kind}',flush=True)
                official=official_fixed_schedule(directory,track,summary,output/'audit-runtime'/f'{entry["id"]}-{kind}')
                error=abs(official['total']-summary['total'])
                if error>2e-5 or official['counts']['observe_actions']!=len(rows):
                    raise AssertionError(f'official runner mismatch {entry["id"]}/{kind}: {error}, {official["counts"]}')
                runs.append(dict(scene=entry['id'],policy=kind,cached_score=summary['total'],official_score=official['total'],error=error))
    result=dict(source='diagnostic only; constant weather proxy was not used for training',samples=len(records),
                maximum_score_error=max(errors,default=0),label_errors=0,records=records,official_runs=runs)
    save_json(output/'audit.json',result)
    return result


def audit_saved_labels(output,samples=200):
    output=Path(output);manifest=json.loads((output/'manifest.json').read_text());rng=np.random.default_rng(663100)
    records=[]
    for _ in range(samples):
        entry=manifest['entries'][int(rng.integers(len(manifest['entries'])))];directory=output/'scenarios'/entry['id'];track=int(rng.integers(2))
        scenario=load_scenario(directory/'scenario.json');rows=json.loads((directory/f'schedule-{track}.json').read_text())
        position=int(rng.integers(len(rows)));row=rows[position];weather=WeatherTruth(scenario.slots,[e for e in scenario.events if e.event_type=='instrument_fault'])
        reports=json.loads((directory/f'training-simulation-{track}.json').read_text())['reports']
        for report in reports:
            at=utc(report['issued_at_utc'])
            if at<=utc(row['start']):weather.report_fault(at,scenario.score_config)
        expected=int(fault_label(weather,utc(row['end'])))
        answers=json.loads((directory/f'feedback-labels-{track}.json').read_text());actual=answers[position]['label']
        if actual!=expected:raise AssertionError(f'saved label mismatch {entry["id"]}/{track}/{position}')
        records.append(dict(scene=entry['id'],track=track,observe_index=position,expected=expected,saved=actual))
    result=dict(samples=samples,errors=0,records=records,source='official active fault and report/repair state; no label enters classifier inputs')
    save_json(output/'saved_label_audit.json',result)
    return result
