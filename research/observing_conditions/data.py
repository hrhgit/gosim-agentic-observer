"""Self-generated scenarios, exact scorer-compatible labels and grouped datasets."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import subprocess
import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from challenge import v4_weather_simulator as simulator
from challenge.v4_fiber_map import altaz_to_radec, radec_to_altaz
from challenge.v4_scorer import (SlotTruth, ScoreEvent, WeatherTruth, DIRECTIONAL_MULTIPLIER_TYPES,
    FORCE_CLOSE_TYPE, ZERO_SCORE_TYPE, score_target_exposure, program_band, lunar_quality_factor)
from .features import (utc, altaz, geometry, candidate_features, public_context, PublicTimeline, VERSION,
                       CONTEXT_NAMES, CANDIDATE_NAMES, KINDS, minimum_altitude)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT/'artifacts/observing_conditions'
DURATIONS = (60, 300, 600, 900, 1800, 3600)
LEADS = (0, 900, 1800, 3600)
GENERATION_VERSION = 'official-public-calendar-v3-event-durations'


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    temporary.replace(path)


def source_hashes():
    paths = list((ROOT/'challenge').glob('v4*.py')) + list((ROOT/'cards').glob('[abcd]/**/*'))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}


def read_card(card):
    directory = ROOT/'cards'/card
    text = (directory/'public/taskcard.zh.md').read_text().replace('−','-')
    site = {'name': f'formal-{card}-public-site',
            'latitude_deg': float(re.search(r'纬度\s*([-+\d.]+)', text)[1]),
            'longitude_deg': float(re.search(r'经度\s*([-+\d.]+)', text)[1]),
            'utc_offset_hours': 0, 'minimum_altitude_deg': 30.0}
    site['utc_offset_hours'] = round(site['longitude_deg']/15)
    nights = list(csv.DictReader((directory/'public/v4_night_calendar.csv').open()))
    targets = list(csv.DictReader((directory/'public/targets.csv').open()))
    coordinates = np.array([[float(t['ra_deg']), float(t['dec_deg'])] for t in targets])
    score = json.loads((directory/'config/v4_score_config.json').read_text())
    return site, nights, coordinates, score


def make_config(entry):
    site, nights, targets, score = read_card(entry['card'])
    if entry['split'] == 'site_test':
        site.update(name=f'self-generated-site-{entry["site_index"]}', latitude_deg=(-10.0,35.0)[entry['site_index']],
                    longitude_deg=(30.0,-100.0)[entry['site_index']])
        site['utc_offset_hours'] = round(site['longitude_deg']/15)
        start, end = date(2027,1,1), date(2027,6,30)
    else:
        start, end = date.fromisoformat(nights[0]['night_date']), date.fromisoformat(nights[-1]['night_date'])
    config = json.loads((ROOT/'challenge/reference/v4/v4_weather_config.json').read_text())
    config['seed'], config['seed_derivation'] = entry['seed'], 'sha256-v1'
    config['site'] = {k:v for k,v in site.items() if k != 'minimum_altitude_deg'}
    config['survey'].update(start_date=str(start), end_date=str(end+timedelta(days=1)), slot_seconds=900)
    rng = np.random.default_rng(entry['seed'])
    shifted = entry['split'] == 'weather_test' or entry.get('weather_shift', False)
    spread = 0.25 if shifted else 0.10
    low, high = (0.5,1.5) if shifted else (0.75,1.25)
    ratio = ((end-start).days+1)/124
    for key in ('seeing_arcsec','transparency','sky_quality'):
        field = config['quality'][key]
        for parameter in ('nominal','seasonal_amplitude','night_sigma','slot_sigma'):
            field[parameter] *= rng.uniform(1-spread,1+spread)
        field['nominal'] = float(np.clip(field['nominal'],field['minimum'],field['maximum']))
    config['quality']['night_correlation'] = rng.uniform(0.70,0.90)
    config['quality']['slot_correlation'] = rng.uniform(0.80,0.95)
    for definition in config['weather_events']['conditions'].values():
        definition['count'] = max(1,round(definition['count']*ratio*rng.uniform(low,high)))
        multiplier = rng.uniform(low,high)
        definition['duration_slots'] = [max(1,round(v*multiplier)) for v in definition['duration_slots']]
    for key in ('rocket_launch','earthquake','instrument_fault'):
        config[key]['count'] = max(1,round(config[key]['count']*ratio*rng.uniform(low,high)))
        if 'duration_slots' in config[key]:
            multiplier=rng.uniform(low,high)
            config[key]['duration_slots']=[max(1,round(v*multiplier)) for v in config[key]['duration_slots']]
    simulator.validate_config(config)
    return config, site, targets, score


def generate_weather_files(config,directory,entry):
    """Use official event/weather/publication functions with the immutable formal calendar."""
    directory.mkdir(parents=True,exist_ok=True)
    if entry['split']=='site_test':
        simulator.generate(directory.parent/'weather_config.json',directory)
        return
    _,rows,_,_=read_card(entry['card'])
    nights=[];slots=[]
    for row in rows:
        night=simulator.Night(row['night_id'],date.fromisoformat(row['night_date']),
            utc(row['solar_dusk_utc']),utc(row['solar_dawn_utc']),utc(row['observing_start_utc']),
            utc(row['observing_end_utc']),int(row['slot_count']))
        nights.append(night)
        for i in range(night.slot_count):
            slots.append(simulator.Slot(f'{night.night_id}-S{i+1:03}',night.night_id,
                night.observing_start_utc+timedelta(seconds=900*i),900))
    events,effects=simulator.generate_events(config,nights,slots)
    weather=simulator.generate_weather(config,nights,slots,events,effects)
    bulletins=simulator.generate_bulletins(config,nights,slots,events,effects)
    forecasts=simulator.generate_forecasts(config,nights,events)
    shutil.copyfile(ROOT/'cards'/entry['card']/'public/v4_night_calendar.csv',directory/'v4_night_calendar.csv')
    simulator.write_exact_csv(directory/'v4_slots.csv',simulator.SLOT_COLUMNS,(s.csv_row() for s in slots))
    simulator.write_exact_csv(directory/'v4_weather_truth.csv',simulator.WEATHER_COLUMNS,weather)
    simulator.write_exact_csv(directory/'v4_events.csv',simulator.V4_EVENT_COLUMNS,(e.csv_row() for e in events))
    simulator.write_exact_csv(directory/'v4_earthquake_effects.csv',simulator.V4_EARTHQUAKE_EFFECT_COLUMNS,(e.csv_row() for e in effects))
    simulator.write_text_lf(directory/'v4_bulletins.jsonl','\n'.join(bulletins)+'\n')
    simulator.write_text_lf(directory/'v4_forecasts.jsonl','\n'.join(forecasts)+'\n')
    save_json(directory/'generation_source.json',{'weather':'self-generated by official v4 functions',
        'calendar':'byte-identical formal public calendar','generation_version':GENERATION_VERSION,
        'nights':len(nights),'slots':len(slots)})


def read_truth(directory):
    truth = list(csv.DictReader((directory/'v4_weather_truth.csv').open()))
    slots = []
    for row in truth:
        start = utc(row['timestamp_utc'])
        is_open = row['is_observable']=='true'
        slots.append(SlotTruth(row['slot_id'], start, start+timedelta(seconds=int(row['duration_seconds'])),
            is_open, float(row['seeing_arcsec']) if is_open else 0,
            float(row['transparency']) if is_open else 0, float(row['sky_quality']) if is_open else 0, 1.0))
    events = []
    for row in csv.DictReader((directory/'v4_events.csv').open()):
        if row['scope_type'] != 'HORIZON_SECTOR':
            continue  # global effects are already in slot truth; instrument efficiency is standardized
        optional = lambda k: float(row[k]) if row[k] else None
        events.append(ScoreEvent(row['event_id'], row['event_type'], utc(row['actual_start_utc']),
            utc(row['actual_end_utc']), row['scope_type'], optional('azimuth_start_deg'),
            optional('azimuth_end_deg'), optional('min_altitude_deg'), optional('max_altitude_deg'),
            float(row['seeing_multiplier']),float(row['transparency_multiplier']),
            float(row['sky_quality_multiplier']),1.0,row['force_close']=='true'))
    return WeatherTruth(slots, None), events


def pieces(weather, events, start, end, cut_events):
    result = []
    cursor = start
    for seconds, truth in weather.segments(start,end):
        segment_end = cursor+timedelta(seconds=seconds)
        cuts = {cursor,segment_end}
        if cut_events:
            for event in events:
                for boundary in (event.actual_start_utc,event.actual_end_utc):
                    if cursor < boundary < segment_end:
                        cuts.add(boundary)
        boundaries = sorted(cuts)
        for left,right in zip(boundaries,boundaries[1:]):
            while left < right:
                piece_end = min(right,left+timedelta(seconds=120))
                result.append((left+(piece_end-left)/2,(piece_end-left).total_seconds(),truth))
                left = piece_end
        cursor = segment_end
    return result


def labels(ra,dec,start,end,weather,events,site,score):
    """Vectorized official midpoint integration, keeping closed time in the denominator."""
    duration = (end-start).total_seconds()
    active_events = [e for e in events if e.overlaps(start,end)]
    quality, usable, band_quality = np.zeros(len(ra)),np.zeros(len(ra)),np.zeros(len(ra))
    for moment,seconds,truth in pieces(weather,active_events,start,end,True):
        if truth is None or not truth.is_observable:
            continue
        _,alt,az,g = geometry(ra,dec,moment,site,score)
        valid = np.ones(len(ra),dtype=bool)
        multiplier = np.ones(len(ra))
        for event in active_events:
            if not event.actual_start_utc <= moment < event.actual_end_utc:
                continue
            az_ok = ((az>=event.azimuth_start_deg)&(az<=event.azimuth_end_deg)) if event.azimuth_start_deg<=event.azimuth_end_deg else ((az>=event.azimuth_start_deg)|(az<=event.azimuth_end_deg))
            covered = az_ok&(alt>=event.min_altitude_deg)&(alt<=event.max_altitude_deg)
            if event.force_close or event.event_type in (ZERO_SCORE_TYPE, FORCE_CLOSE_TYPE):
                valid &= ~covered
            if event.event_type in DIRECTIONAL_MULTIPLIER_TYPES:
                multiplier *= np.where(covered,event.transparency_multiplier*event.sky_quality_multiplier/event.seeing_multiplier,1)
        quality += seconds*valid*g*(truth.transparency*truth.sky_quality/truth.seeing_arcsec)*multiplier
        usable += seconds*valid
    for moment,seconds,truth in pieces(weather,[],start,end,False):
        if truth is not None and truth.is_observable:
            g = geometry(ra,dec,moment,site,score)[-1]
            band_quality += seconds*g*(truth.transparency*truth.sky_quality/truth.seeing_arcsec)
    quality,usable,band_quality = quality/duration,usable/duration,band_quality/duration
    bands = np.where(band_quality>=score['program']['bands']['DARK'],0,
                     np.where(band_quality>=score['program']['bands']['BRIGHT'],1,2))
    return np.column_stack([quality,usable,usable<0.5,bands]).astype(np.float32)


def scenario_entries():
    entries = []
    for card_index,card in enumerate('abcd'):
        splits = ['train']*56+['validation']*8+['calibration']*8+['test']*8
        for index,split in enumerate(splits):
            entries.append(dict(id=f'{card}-{index:03}',card=card,split=split,seed=730000+1000*card_index+index))
        for index in range(8):
            entries.append(dict(id=f'{card}-shift-{index:03}',card=card,split='weather_test',seed=740000+1000*card_index+index))
    for index in range(32):
        entries.append(dict(id=f'site-{index:03}',card='abcd'[index%4],split='site_test',site_index=index%2,seed=750000+index))
    return entries


def generate_one(arguments):
    entry, output, groups = arguments
    directory = Path(output)/'scenarios'/entry['id']
    config,site,catalogue,score = make_config(entry)
    signature = hashlib.sha256(json.dumps({'config':config,'entry':entry,'groups':groups,'features':VERSION,
        'generation_version':GENERATION_VERSION},sort_keys=True).encode()).hexdigest()
    if (directory/'complete.json').exists():
        complete = json.loads((directory/'complete.json').read_text())
        if complete['signature'] != signature:
            raise ValueError(f'scenario signature mismatch: {entry["id"]}')
        return complete
    directory.mkdir(parents=True,exist_ok=True)
    save_json(directory/'weather_config.json',config)
    generate_weather_files(config,directory/'truth_and_public',entry)
    source = directory/'truth_and_public'
    weather,events = read_truth(source)
    bulletins = [json.loads(line) for line in (source/'v4_bulletins.jsonl').read_text().splitlines()]
    forecasts = [json.loads(line) for line in (source/'v4_forecasts.jsonl').read_text().splitlines()]
    timeline = PublicTimeline(bulletins,forecasts)
    nights = list(csv.DictReader((source/'v4_night_calendar.csv').open()))
    night_map = {n['night_id']:n for n in nights}
    rng = np.random.default_rng(entry['seed']+1000000)
    contexts,histories,candidates,answers,coordinates,metadata = [],[],[],[],[],[]
    while len(contexts) < groups:
        slot = weather.slots[int(rng.integers(len(weather.slots)))]
        night = night_map[slot.slot_id.split('-S')[0]]
        now = slot.start_utc+timedelta(seconds=int(rng.integers(900)))
        lead = int(rng.choice(LEADS))
        start = now+timedelta(seconds=lead)
        night_end = utc(night['observing_end_utc'])
        if start >= night_end:
            continue
        requested = int(rng.choice(DURATIONS))
        end = min(start+timedelta(seconds=requested),night_end)
        eligible = minimum_altitude(catalogue[:,0],catalogue[:,1],start,end,site) >= site['minimum_altitude_deg']
        valid_indices = np.flatnonzero(eligible)
        if len(valid_indices) < 24:
            continue
        selection = rng.choice(valid_indices,24,replace=False)
        coords = list(catalogue[selection])
        while len(coords) < 32:
            altitude = math.degrees(math.asin(rng.uniform(math.sin(math.radians(30)),1)))
            azimuth = float(rng.uniform(0,360))
            ra,dec = altaz_to_radec(altitude,azimuth,start,site['latitude_deg'],site['longitude_deg'])
            _,ok = candidate_features(np.array([ra]),np.array([dec]),start,end,site,score)
            if ok[0]:
                coords.append([ra,dec])
        coords = np.asarray(coords)
        x,_ = candidate_features(coords[:,0],coords[:,1],start,end,site,score)
        context,history = timeline.snapshot(now,start,requested,(end-start).total_seconds(),night['night_date'],night_end,site,score)
        y = labels(coords[:,0],coords[:,1],start,end,weather,events,site,score)
        contexts.append(context); histories.append(history); candidates.append(x); answers.append(y); coordinates.append(coords)
        metadata.append(dict(now=now.isoformat(),start=start.isoformat(),end=end.isoformat(),night_date=night['night_date'],
            night_end=night_end.isoformat(),lead=lead,requested=requested))
    np.savez_compressed(directory/'samples.npz',context=np.array(contexts),history=np.array(histories),
        candidate=np.array(candidates),y=np.array(answers),coordinates=np.array(coordinates))
    save_json(directory/'metadata.json',dict(entry=entry,site=site,score=score,groups=metadata))
    complete = dict(**entry,signature=signature,groups=groups,samples=groups*32,source='self-generated, NOT official hidden weather')
    save_json(directory/'complete.json',complete)
    return complete


def generate_dataset(output=DEFAULT_OUTPUT,workers=4,pilot=False):
    output=Path(output)
    entries=scenario_entries()
    if pilot:
        entries=[e for e in entries if e['split']=='train' and int(e['id'].split('-')[-1])<2]
    groups=8 if pilot else 128
    source=source_hashes()
    manifest_path=output/'manifest.json'
    if manifest_path.exists() and json.loads(manifest_path.read_text())['sources']!=source:
        raise ValueError('official source files changed; create a new dataset directory')
    save_json(manifest_path,dict(version=VERSION,generation_version=GENERATION_VERSION,sources=source,git_revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        source='formal public A-D + explicitly self-generated weather',entries=entries,groups_per_scene=groups,
        context_names=CONTEXT_NAMES,candidate_names=CANDIDATE_NAMES,history_shape=[32,75],quality_definition='Q_env, efficiency=1, no instrument faults',pilot=pilot))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending=[pool.submit(generate_one,(e,str(output),groups)) for e in entries]
        for count,future in enumerate(as_completed(pending),1):
            result=future.result()
            print(f'generated {count}/{len(entries)} {result["id"]}: {result["samples"]} samples',flush=True)
    save_json(output/'generation_complete.json',dict(scenarios=len(entries),samples=len(entries)*groups*32))


def load_dataset(output,splits):
    output=Path(output)
    manifest=json.loads((output/'manifest.json').read_text())
    fields={k:[] for k in ('context','history','candidate','y')}
    scene_ids=[]; cards=[]
    for entry in manifest['entries']:
        if entry['split']!='site_test':
            official=ROOT/'cards'/entry['card']/'public/v4_night_calendar.csv'
            generated=output/'scenarios'/entry['id']/'truth_and_public/v4_night_calendar.csv'
            if official.read_bytes()!=generated.read_bytes():
                raise AssertionError(f'public calendar changed: {entry["id"]}')
        if entry['split'] not in splits:
            continue
        with np.load(output/'scenarios'/entry['id']/'samples.npz') as arrays:
            for key in fields:
                fields[key].append(arrays[key])
            scene_ids.extend([entry['id']]*len(arrays['context'])); cards.extend([entry['card']]*len(arrays['context']))
    if not scene_ids:
        raise ValueError(f'no dataset for splits: {splits}')
    return {k:np.concatenate(v) for k,v in fields.items()} | {'scene':np.array(scene_ids),'card':np.array(cards)}


def audit_dataset(output,count=200,sample_splits=('train',)):
    output=Path(output)
    manifest=json.loads((output/'manifest.json').read_text())
    seed_ids=[e['seed'] for e in manifest['entries']]
    if len(set(seed_ids))!=len(seed_ids):
        raise AssertionError('duplicate scene seeds')
    scene_ids=[e['id'] for e in manifest['entries']]
    if len(set(scene_ids))!=len(scene_ids):
        raise AssertionError('a scene occurs in multiple dataset partitions')
    if manifest['sources']!=source_hashes():
        raise AssertionError('formal public inputs or official generator changed')
    total=0
    for entry in manifest['entries']:
        if entry['split']!='site_test':
            official=ROOT/'cards'/entry['card']/'public/v4_night_calendar.csv'
            generated=output/'scenarios'/entry['id']/'truth_and_public/v4_night_calendar.csv'
            assert official.read_bytes()==generated.read_bytes()
        with np.load(output/'scenarios'/entry['id']/'samples.npz') as arrays:
            for key in ('context','history','candidate','y','coordinates'):
                if not np.isfinite(arrays[key]).all():
                    raise AssertionError(f'nonfinite {key}: {entry["id"]}')
            assert ((arrays['y'][...,1]>=0)&(arrays['y'][...,1]<=1)).all()
            assert np.array_equal(arrays['y'][...,2],arrays['y'][...,1]<0.5)
            assert (arrays['candidate'][...,-2]*90>=30-1e-5).all()
            total+=arrays['y'].shape[0]*arrays['y'].shape[1]
    entries=[e for e in manifest['entries'] if e['split'] in sample_splits]
    if count and not entries:
        raise ValueError('No scenes in requested audit splits')
    rng=np.random.default_rng(20261004)
    maximum_error=0.0
    for index in range(count):
        entry=entries[index%len(entries)]
        directory=output/'scenarios'/entry['id']
        metadata=json.loads((directory/'metadata.json').read_text())
        with np.load(directory/'samples.npz') as arrays:
            group=int(rng.integers(len(arrays['context']))); point=int(rng.integers(32))
            ra,dec=arrays['coordinates'][group,point]; answer=arrays['y'][group,point]
            saved_context,saved_history=arrays['context'][group],arrays['history'][group]
        info=metadata['groups'][group]
        start,end=utc(info['start']),utc(info['end'])
        source=directory/'truth_and_public'
        bulletins=[json.loads(line) for line in (source/'v4_bulletins.jsonl').read_text().splitlines()]
        forecasts=[json.loads(line) for line in (source/'v4_forecasts.jsonl').read_text().splitlines()]
        expected_context,expected_history=public_context(info['now'],start,info['requested'],
            (end-start).total_seconds(),info['night_date'],info['night_end'],metadata['site'],metadata['score'],
            bulletins,forecasts)
        np.testing.assert_allclose(saved_context,expected_context,atol=1e-6)
        np.testing.assert_allclose(saved_history,expected_history,atol=1e-6)
        weather,events=read_truth(directory/'truth_and_public')
        target=dict(target_id='audit',ra_deg=float(ra),dec_deg=float(dec),feature_flux=1.0,science_weight=1.0)
        func=lambda t: radec_to_altaz(float(ra),float(dec),t,metadata['site']['latitude_deg'],metadata['site']['longitude_deg'])
        truth=score_target_exposure(target,list(weather.segments(start,end)),[e for e in events if e.overlaps(start,end)],
            weather,int((end-start).total_seconds()),1.0,metadata['score'],start,func,metadata['site'])
        expected_band=program_band(weather.band_quality(start,end,metadata['score'],lambda t:func(t)[0],
            lambda t: lunar_quality_factor(float(ra),float(dec),t,metadata['site'],metadata['score'])),metadata['score'])
        assert int(answer[3])==('DARK','BRIGHT','BACKUP').index(expected_band)
        error=abs(truth.quality-float(answer[0])); maximum_error=max(maximum_error,error)
        if error>2e-6:
            raise AssertionError(f'official quality mismatch {entry["id"]}: {error}')
        # Independently integrate openness using scorer event coverage, not vectorized label masks.
        seconds_open=0
        for moment,seconds,slot in pieces(weather,events,start,end,True):
            alt,az=func(moment)
            closed=any(e.actual_start_utc<=moment<e.actual_end_utc and e.covers_altaz(alt,az) and
                (e.force_close or e.event_type in (ZERO_SCORE_TYPE,FORCE_CLOSE_TYPE)) for e in events)
            if slot and slot.is_observable and not closed:
                seconds_open+=seconds
        assert abs(seconds_open/(end-start).total_seconds()-float(answer[1]))<1e-6
    result=dict(checked=count,sample_splits=list(sample_splits),public_feature_boundary_checks=count,structurally_checked_samples=total,
        maximum_quality_error=maximum_error,unique_scene_seeds=len(seed_ids),passed=True)
    save_json(output/'audit.json',result)
    print(json.dumps(result),flush=True)
