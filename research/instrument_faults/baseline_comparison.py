"""Rule baseline vs small-scale fault models on the same self-generated scenes.

Open loop: the fixed feedback of the validation scenes is replayed through the
baseline agent's own estimator (`agents/claude-baseline-v1`, imported unmodified)
and its belief after each exposure is scored exactly like the classifiers.
Closed loop: each policy decides its own reports on the same fixed schedules and
a correct report repairs the instrument for the rest of the trace.
"""
from __future__ import annotations

import argparse
import bisect
import os
from concurrent.futures import ProcessPoolExecutor
from datetime import date
import json
import math
import multiprocessing
from pathlib import Path
import sys
import time
import types

import joblib
import numpy as np
from threadpoolctl import threadpool_limits

from challenge.v4_runner import _data_loss_plan
from challenge.v4_scorer import WeatherTruth, BestLedger, required_penalty, uniformity_penalty
from research.observing_conditions.data import save_json
from research.observing_conditions.features import utc
from . import benchmark, feedback_only as feedback
from .simulation import exposure_scores, fault_label, load_assets

VERSION = 'fault-baseline-comparison-v1'
ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT/'agents/claude-baseline-v1/agent'
# The fixed schedules space exposures 60-80 minutes apart, so the baseline's
# "last hour" reference for a sudden drop never finds the previous exposure.
# The second variant widens that one constant; it was fixed before any result.
VARIANTS = {'baseline': None, 'baseline_2h': 7200.0}
KINDS = ('boosting', 'logistic')
FREE = (.5, .6, .7, .8, .9)
PAID = (.6, .7, .8, .9, .95, .99)
THRESHOLDS = [(f, p) for f in FREE for p in PAID if p >= f]
FALSE_TARGET, PAID_FALSE_TARGET = .5, .1
N_FEEDBACK = len(feedback.FEATURE_NAMES)


def baseline_sky(window=None):
    """The baseline's Sky class; `window` replaces its one-hour drop reference."""
    if str(AGENT) not in sys.path:
        sys.path.insert(0, str(AGENT))
    from core import astro, conditions
    if window is None:
        return astro, conditions.Sky
    source = (AGENT/'core/conditions.py').read_text()
    old = 't0 - entry[0] <= 3600.0'
    if source.count(old) != 1:
        raise ValueError('baseline drop-reference window not found exactly once')
    module = types.ModuleType('core.conditions_window')
    module.__package__ = 'core'
    exec(compile(source.replace(old, f't0 - entry[0] <= {float(window)!r}'), 'conditions.py', 'exec'), module.__dict__)
    return astro, module.Sky


def geometry(scenario, rows):
    """Per exposure, the public quantities the baseline computes when it plans one."""
    astro, _ = baseline_sky()
    score = scenario.score_config
    site = astro.Site(scenario.site['latitude_deg'], scenario.site['longitude_deg'],
                      scenario.site.get('minimum_altitude_deg', 30.0), score.get('airmass_exponent', .6),
                      score.get('lunar_model', {}))
    f0t0 = score['flux_zero_point']*score['exposure_zero_point_seconds']
    vectors = {}
    result = []
    for row in rows:
        t0 = utc(row['start']).timestamp(); teff = float(row['duration'])
        frames = [site.frame(t0+teff*x) for x in (1/6, .5, 5/6)]
        moons = [site.moon(t0+teff*x) for x in (1/6, .5, 5/6)]
        _, zen, north, east = frames[1]
        targets = []
        for index in row['target_indices']:
            target = scenario.targets[index]
            if index not in vectors:
                vectors[index] = astro.unit_vector(target['ra_deg'], target['dec_deg'])
            x, y, z = vectors[index]
            a = sum(site.transmission(x*f[1][0]+y*f[1][1]+z*f[1][2])*site.lunar_factor(x, y, z, m)
                    for f, m in zip(frames, moons))/3
            alt = math.asin(max(-1.0, min(1.0, x*zen[0]+y*zen[1]+z*zen[2])))*astro.DEG
            az = math.atan2(x*east[0]+y*east[1]+z*east[2], x*north[0]+y*north[1]+z*north[2])*astro.DEG % 360.0
            targets.append((index, target['feature_flux']*teff*a/f0t0, a, alt, az, target['science_weight']))
        result.append((t0, teff, date.fromisoformat(row['night_date']).toordinal(), targets))
    return result


class Engine:
    """Organizer side of one fixed-schedule trace: feedback, reports, repairs, settlement."""

    def __init__(self, scenario, rows, physics):
        self.scenario, self.rows, self.physics = scenario, rows, physics
        self.faults = [e for e in scenario.events if e.event_type == 'instrument_fault']
        self.weather = WeatherTruth(scenario.slots, self.faults)
        self.ledger = BestLedger(); self.settlement = 0.0; self.reports = []
        self.messages = sorted(scenario.bulletins+scenario.forecasts, key=lambda m: utc(m['issued_at_utc']))
        self.times = [utc(m['issued_at_utc']) for m in self.messages]; self.cursor = 0
        self.loss = _data_loss_plan(scenario); self.loss_done = False
        self.allowance = scenario.score_config['reporting']['false_report_free_allowance']

    def deliver(self, now, observed_count):
        last = bisect.bisect_right(self.times, now)
        batch = self.messages[self.cursor:last]; self.cursor = last
        if self.loss and not self.loss_done and now >= self.loss.trigger_utc:
            first = math.floor(self.loss.window_start_fraction*observed_count)
            end = math.floor(self.loss.window_end_fraction*observed_count)
            self.ledger.invalidate_window(first, end, self.loss.event_id)
            batch = batch+[dict(record_type='state_resync', issued_at_utc=now.isoformat(),
                                invalidated_window=dict(action_index_start=first, action_index_end_exclusive=end))]
            self.loss_done = True
        return batch

    def report(self, now):
        event = self.weather.active_fault(now)
        correct = fault_label(self.weather, now)
        delta = self.weather.report_fault(now, self.scenario.score_config)
        self.settlement += delta
        result = dict(record_type='report_result', issued_at_utc=now.isoformat(), correct=correct,
                      repaired=correct, score_delta=delta)
        self.reports.append(dict(result, event_id=event.event_id if correct else None))
        return result

    def expose(self, row):
        scores, factors, _, hit_mask = exposure_scores(self.scenario, row, self.physics, self.weather)
        targets = [self.scenario.targets[i] for i in row['target_indices']]
        start, end = utc(row['start']), utc(row['end'])
        for target, factor, value, hit in zip(targets, factors, scores, hit_mask):
            if hit:
                self.ledger.record(row['observe_index'], target['target_id'], float(factor), float(value), start, end)
        public = {k: row[k] for k in ('observe_index', 'start', 'end', 'night', 'duration', 'program', 'altitude', 'azimuth')}
        public['targets'] = [{k: t[k] for k in ('target_id', 'feature_flux', 'science_weight', 'ra_deg', 'dec_deg')} for t in targets]
        public['result'] = dict(assigned_count=len(targets), hit_count=int(hit_mask.sum()),
                                hits=[dict(target_id=t['target_id'], score=round(float(s), 6))
                                      for t, s, hit in zip(targets, scores, hit_mask) if hit])
        by_index = {i: round(float(s), 6) for i, s, hit in zip(row['target_indices'], scores, hit_mask) if hit}
        usable = any(hit and t['science_weight'] > 0 and t['feature_flux'] > 0 for t, hit in zip(targets, hit_mask))
        return public, by_index, usable

    @property
    def free(self):
        return self.weather.false_reports_since_correct < self.allowance

    def totals(self):
        best = self.ledger.best(); factors = self.ledger.max_factors()
        missing, required = required_penalty(self.scenario.targets, factors, self.scenario.score_config)
        _, uniform = uniformity_penalty(self.scenario.targets, factors, self.scenario.score_config)
        science = sum(v[1] for v in best.values())
        return dict(total=science-required-uniform+self.settlement, required_missing=missing,
                    components=dict(science=science, required_penalty=-required, uniformity_penalty=-uniform,
                                    report_settlement=self.settlement))


class BaselineJudge:
    """Drives the baseline's Sky the way its agent loop does, on exposures it did not choose."""

    def __init__(self, scenario, rows, shapes, window=None, final_probe=False):
        _, sky = baseline_sky(window)
        self.sky = sky(scenario.score_config)
        self.rows, self.shapes, self.final_probe = rows, shapes, final_probe
        self.slot = 900.0
        self.best = {}; self.rate_gain = 0.0; self.rate_time = 0.0
        # Science still to come: the agent multiplies its recent rate by the night time
        # left; a fixed schedule only has its own remaining exposure seconds.
        self.remaining = np.r_[np.cumsum([s[1] for s in shapes][::-1])[::-1], 0.0]
        self.rec = None

    def messages(self, batch, now):
        for message in batch:
            if message['record_type'] == 'bulletin':
                self.sky.bulletin(message, utc(message['issued_at_utc']).timestamp())

    def _gain(self, position):
        return .5*self.rate_gain/self.rate_time*float(self.remaining[position]) if self.rate_time else 0.0

    def before(self, position, now, free):
        if self.final_probe and position == len(self.rows)-1 and self.sky.free_left >= 1:
            return True
        return self.sky.want_report(now.timestamp(), self._gain(position))

    def plan(self, position):
        t0, teff, night, targets = self.shapes[position]
        sky = self.sky
        directional = sky.directional
        items = [(index, 0, 0, unit, a, sky.dir_factor(alt, az) if directional else 1.0, alt, az, True, weight)
                 for index, unit, a, alt, az, weight in targets]
        self.rec = dict(t0=t0, teff=teff, program=self.rows[position]['program'], items=items, night=night,
                        clear_dirs=all(item[5] >= .999 for item in items), notices=frozenset())
        return sky.levels(), items

    def feedback(self, position, public, hits, batch, end, free):
        self.messages(batch, end)
        rec, sky = self.rec, self.sky
        rec['notices'] = sky.notices_between(rec['t0'], rec['t0']+rec['teff'], self.slot)
        sky.update(rec, hits, rec['night'])
        gain = 0.0
        for index, score in hits.items():
            if score > self.best.get(index, 0.0):
                gain += score-self.best.get(index, 0.0); self.best[index] = score
        if not (hits and all(score <= 0.0 for score in hits.values())):
            decay = math.exp(-rec['teff']/(6.0*3600.0))
            self.rate_gain = self.rate_gain*decay+gain; self.rate_time = self.rate_time*decay+rec['teff']
        return sky.want_report(end.timestamp(), self._gain(position+1))

    def reported(self, result, now):
        self.sky.report_result(bool(result['correct']), now.timestamp())

    def belief(self):
        """(counts as low efficiency, any instrument suspicion) after the last exposure."""
        sky = self.sky
        return bool(sky.suspect and sky.e < .78*sky.e_ref), bool(sky.suspect)


class ModelJudge:
    """The planned report rule for a classifier: one decision per three new usable exposures."""

    def __init__(self, scenario, model, thresholds, extra, final_probe=False):
        self.state = feedback.FeedbackEvidence(scenario.score_config)
        self.model, self.thresholds, self.extra, self.final_probe = model, thresholds, extra, final_probe
        self.new = set(); self.last = len(extra)-1; self.decisions = []

    def messages(self, batch, now):
        self.state.messages(batch, now)
        for message in batch:
            if message['record_type'] == 'state_resync':
                window = message['invalidated_window']
                self.new.difference_update(range(window['action_index_start'], window['action_index_end_exclusive']))

    def before(self, position, now, free):
        return self.final_probe and position == self.last and free

    def plan(self, position):
        return None

    def vector(self, position, end):
        return np.r_[self.state.vector(end), self.extra[position]].astype(np.float32)

    def feedback(self, position, public, hits, batch, end, free):
        self.state.observation(public)
        self.messages(batch, end)
        records = self.state.records
        if records and records[-1]['observe_index'] == public['observe_index'] and records[-1]['stats']['usable_targets'] > 0:
            self.new.add(public['observe_index'])
        if self.state.info(end)['status'] != 'ready' or len(self.new) < 3:
            return False
        self.new.clear()
        p = float(self.model.predict_proba(self.vector(position, end)[None])[0])
        self.decisions.append((p, free))
        return p >= self.thresholds[0 if free else 1]

    def reported(self, result, now):
        self.state.messages([result], now)
        self.new.clear()


def closed_loop(assets, policy, judge=None, opportunities=None):
    scenario, rows, physics = assets
    engine = Engine(scenario, rows, physics)
    seen = {f.event_id: [] for f in engine.faults}
    for position, row in enumerate(rows):
        start, end = utc(row['start']), utc(row['end'])
        batch = engine.deliver(start, row['observe_index'])
        if policy == 'oracle':
            if fault_label(engine.weather, start):
                engine.report(start)
        elif judge:
            judge.messages(batch, start)
            if judge.before(position, start, engine.free):
                judge.reported(engine.report(start), start)
            judge.plan(position)
        public, hits, usable = engine.expose(row)
        batch = engine.deliver(end, row['observe_index']+1)
        event = engine.weather.active_fault(end)
        if usable and event:
            seen[event.event_id].append(end)
        if judge and judge.feedback(position, public, hits, batch, end, engine.free):
            judge.reported(engine.report(end), end)
    events = []
    for event in engine.faults:
        found = next((r for r in engine.reports if r['correct'] and r['event_id'] == event.event_id), None)
        available = seen[event.event_id] if opportunities is None else [utc(t) for t in opportunities[event.event_id]]
        at = utc(found['issued_at_utc']) if found else None
        events.append(dict(event_id=event.event_id, detected=bool(found), eligible=len(available) >= 3,
                           opportunities=len(available), delay_exposures=sum(t <= at for t in available) if at and available else None,
                           delay_seconds=max(0, (at-available[0]).total_seconds()) if at and available else None,
                           multiplier=event.instrument_efficiency_multiplier))
    reports = engine.reports
    return engine.totals() | dict(policy=policy, reports=reports, correct=sum(r['correct'] for r in reports),
                                  false=sum(not r['correct'] for r in reports),
                                  paid_false=sum(not r['correct'] and r['score_delta'] < 0 for r in reports),
                                  events=events, exposures=len(rows),
                                  event_opportunities={k: [t.isoformat() for t in v] for k, v in seen.items()})


def open_loop(assets, track, shapes, check_model=None):
    """The training-time report plan (none / four scheduled), watched by every baseline variant."""
    scenario, rows, physics = assets
    engine = Engine(scenario, rows, physics)
    judges = {name: BaselineJudge(scenario, rows, shapes, window) for name, window in VARIANTS.items()}
    watcher = ModelJudge(scenario, None, None, check_model) if check_model is not None else None
    plan = [utc(rows[max(0, math.ceil(len(rows)*f)-1)]['start']) for f in (.25, .5, .75, 1)] if track and rows else []
    cursor = 0; labels = []; beliefs = {name: [] for name in judges}; vectors = []; nowcast = []
    for position, row in enumerate(rows):
        start, end = utc(row['start']), utc(row['end'])
        batch = engine.deliver(start, row['observe_index'])
        for judge in list(judges.values())+([watcher] if watcher else []):
            judge.messages(batch, start)
        while cursor < len(plan) and start >= plan[cursor]:
            result = engine.report(start); cursor += 1
            for judge in list(judges.values())+([watcher] if watcher else []):
                judge.reported(result, start)
        first, last = physics['pointers'][row['observe_index']:row['observe_index']+2]
        for name, judge in judges.items():
            (_, level), items = judge.plan(position)
            if name == 'baseline' and items:
                # What the baseline would plan with for these targets, before the exposure.
                a = np.array([item[4] for item in items]); d = np.array([item[5] for item in items])
                nowcast.append(np.column_stack([np.full(len(items), position), level*a*d, level*a,
                                                physics['environment'][first:last], physics['band'][first:last],
                                                np.full(len(items), int(fault_label(engine.weather, start)))]))
        public, hits, _ = engine.expose(row)
        batch = engine.deliver(end, row['observe_index']+1)
        for name, judge in judges.items():
            judge.feedback(position, public, hits, batch, end, engine.free)
            beliefs[name].append(judge.belief())
        if watcher:
            watcher.state.observation(public); watcher.messages(batch, end)
            vectors.append(watcher.state.vector(end))
        labels.append(int(fault_label(engine.weather, end)))
    return dict(label=np.asarray(labels, np.int8), beliefs={k: np.asarray(v, bool) for k, v in beliefs.items()},
                vectors=np.asarray(vectors) if watcher else None,
                nowcast=np.concatenate(nowcast) if nowcast else np.empty((0, 6)))


def scene_job(arguments):
    data, inputs, models, output, entry, mode, pairs = arguments
    path = Path(output)/'scenes'/mode/f'{entry["id"]}.json'
    if path.exists():
        return json.loads(path.read_text())
    started = time.perf_counter()
    with np.load(Path(inputs)/'inputs'/entry['id']/'features.npz') as arrays:
        stored = {k: arrays[k] for k in arrays.files}
    classifiers = {kind: joblib.load(Path(models)/kind/'classifier.joblib') for kind in KINDS}
    record = dict(scene=entry['id'], card=entry['card'], fault_count=entry['fault_count'], runs=[])
    flags = {name: [] for name in VARIANTS}; suspects = {name: [] for name in VARIANTS}; labels = []
    with threadpool_limits(limits=1):
        for track in (0, 1):
            assets = load_assets(Path(data)/'scenarios'/entry['id'], track)
            rows = assets[1]
            mask = stored['track'] == track
            extra = stored['x'][mask][:, N_FEEDBACK:]
            if len(extra) != len(rows) or not np.array_equal(stored['observe_index'][mask], np.arange(len(rows))):
                raise ValueError('saved public inputs do not line up with the schedule')
            shapes = geometry(assets[0], rows) if mode in ('validation', 'selection-rules') else None
            if mode == 'validation':
                watched = open_loop(assets, track, shapes, extra if entry.get('check') else None)
                if not np.array_equal(watched['label'], stored['y'][mask]):
                    raise AssertionError('replayed answers differ from the saved classifier answers')
                if watched['vectors'] is not None and not np.allclose(
                        watched['vectors'], stored['x'][mask][:, :N_FEEDBACK], equal_nan=True, rtol=1e-5, atol=1e-6):
                    raise AssertionError('replayed classifier inputs differ from the saved ones')
                labels.append(watched['label'])
                for name in VARIANTS:
                    flags[name].append(watched['beliefs'][name][:, 0]); suspects[name].append(watched['beliefs'][name][:, 1])
                directory = Path(output)/'nowcast'; directory.mkdir(parents=True, exist_ok=True)
                np.save(directory/f'{entry["id"]}-{track}.npy', watched['nowcast'].astype(np.float32))

            def run(name, policy, judge=None, thresholds=None):
                summary = closed_loop(assets, policy, judge, opportunities)
                summary.update(scene=entry['id'], card=entry['card'], track=track, model=name,
                               thresholds=list(thresholds) if thresholds else [])
                record['runs'].append(summary)
                return summary

            opportunities = None
            opportunities = run('none', 'none')['event_opportunities']
            if shapes is not None:
                run('oracle', 'oracle')
                for name, window in VARIANTS.items():
                    run(name, 'baseline', BaselineJudge(assets[0], rows, shapes, window))
                    if mode == 'validation':
                        run(name+'+final', 'baseline', BaselineJudge(assets[0], rows, shapes, window, True))
            for kind in KINDS:
                # Threshold pairs that answer every decision of an earlier run the same way
                # follow the same trajectory; reuse it instead of simulating again.
                done = []
                for pair in pairs[kind]:
                    same = next((s for d, q, s in done if all((p >= pair[not free]) == (p >= q[not free]) for p, free in d)), None)
                    if same is not None:
                        record['runs'].append(dict(same, thresholds=list(pair)))
                        continue
                    judge = ModelJudge(assets[0], classifiers[kind], pair, extra)
                    done.append((judge.decisions, pair, run(kind, 'model', judge, pair)))
                if mode == 'validation' and len(pairs[kind]) == 1:
                    run(kind+'+final', 'model', ModelJudge(assets[0], classifiers[kind], pairs[kind][0], extra, True), pairs[kind][0])
    if mode == 'validation':
        valid = stored['valid']
        y = np.concatenate(labels)[valid]
        p = {kind: classifiers[kind].predict_proba(stored['x'][valid]) for kind in KINDS}
        record['rows'] = int(valid.sum())
        record['counts'] = {name: confusion(y, np.concatenate(flags[name])[valid]) for name in VARIANTS}
        record['counts'] |= {name+':suspect': confusion(y, np.concatenate(suspects[name])[valid]) for name in VARIANTS}
        record['counts'] |= {kind: confusion(y, p[kind] >= .5) for kind in KINDS}
        record['counts']['always_no_fault'] = confusion(y, np.zeros(len(y), bool))
    record['seconds'] = time.perf_counter()-started
    save_json(path, record)
    return record


def confusion(y, positive):
    """Scene-normalised counts, so that summing scenes weights each scene equally."""
    n = len(y)
    return dict(tp=float(((y == 1) & positive).sum()/n), fn=float(((y == 1) & ~positive).sum()/n),
                fp=float(((y == 0) & positive).sum()/n), tn=float(((y == 0) & ~positive).sum()/n))


def rates(records, name, repetitions=2000):
    counts = np.asarray([[r['counts'][name][k] for k in ('tp', 'fn', 'fp', 'tn')] for r in records])
    ratio = lambda a, b: float(a/b) if b else None
    tp, fn, fp, tn = counts.sum(0)
    result = dict(accuracy=ratio(tp+tn, tp+tn+fp+fn), recall=ratio(tp, tp+fn), precision=ratio(tp, tp+fp),
                  false_positive_rate=ratio(fp, fp+tn))
    rng = np.random.default_rng(510051)
    a, b, c, d = counts[rng.integers(len(counts), size=(repetitions, len(counts)))].sum(1).T
    for metric, top, bottom in (('accuracy', a+d, a+b+c+d), ('recall', a, a+b), ('precision', a, a+c),
                                ('false_positive_rate', c, c+d)):
        values = np.divide(top, bottom, out=np.full(repetitions, np.nan), where=bottom > 0)
        result[metric+'_ci95'] = np.nanquantile(values, [.025, .975]).tolist() if np.isfinite(values).any() else None
    return result


def by_scene(results):
    grouped = {}
    for r in results:
        grouped.setdefault(r['scene'], []).append(r)
    return grouped


def summarize(results):
    """Same counting as evaluation.summarize: scenes weigh equally, ratios pool both traces."""
    scenes = by_scene(results)
    mean = lambda key: float(np.mean([np.mean([r[key] for r in rows]) for rows in scenes.values()]))
    events = [e for r in results for e in r['events']]; eligible = [e for e in events if e['eligible']]
    reports = [p for r in results for p in r['reports']]
    delays = [e['delay_exposures'] for e in events if e['detected'] and e['delay_exposures'] is not None]
    seconds = [e['delay_seconds'] for e in events if e['detected'] and e['delay_seconds'] is not None]
    quantiles = lambda a: dict(p50=float(np.median(a)), p90=float(np.quantile(a, .9))) if a else dict(p50=None, p90=None)
    return dict(scenes=len(scenes), traces=len(results), mean_score=mean('total'), false_per_scene=mean('false'),
                paid_false_per_scene=mean('paid_false'), correct_per_scene=mean('correct'),
                settlement_per_scene=float(np.mean([np.mean([r['components']['report_settlement'] for r in rows]) for rows in scenes.values()])),
                report_count=len(reports), report_precision=sum(p['correct'] for p in reports)/len(reports) if reports else None,
                eligible_events=len(eligible), all_events=len(events),
                eligible_event_recall=sum(e['detected'] for e in eligible)/len(eligible) if eligible else None,
                all_event_recall=sum(e['detected'] for e in events)/len(events) if events else None,
                delay_exposures=quantiles(delays), delay_hours=quantiles([s/3600 for s in seconds]))


def paired(left, right, repetitions=2000):
    a, b = by_scene(left), by_scene(right)
    if set(a) != set(b):
        raise ValueError('paired scenes do not match')
    values = np.array([np.mean([r['total'] for r in a[s]])-np.mean([r['total'] for r in b[s]]) for s in sorted(a)])
    draws = values[np.random.default_rng(41104).integers(len(values), size=(repetitions, len(values)))].mean(1)
    return dict(mean=float(values.mean()), ci95=np.quantile(draws, [.025, .975]).tolist(), scenes=len(values),
                worst_scene=float(values.min()), scenes_below_zero=int((values < 0).sum()))


def tuning_scenes(entries, used):
    """Training scenes the 40-scene models never saw, with the validation mix per card."""
    chosen = []
    for card in 'abcd':
        for count, number in ((0, 4), (1, 8), (3, 4)):
            pool = sorted(e['id'] for e in entries if e['split'] == 'train' and e['card'] == card
                          and e['fault_count'] == count and e['id'] not in used)
            if len(pool) < number:
                raise ValueError('not enough unused training scenes for threshold selection')
            chosen += pool[:number]
    return [e for e in entries if e['id'] in set(chosen)]


def select_thresholds(runs, kind):
    candidates = []
    for pair in THRESHOLDS:
        summary = summarize([r for r in runs if r['model'] == kind and r['thresholds'] == list(pair)])
        candidates.append(dict(thresholds=list(pair), simulation=summary,
                               meets_false_report_targets=summary['false_per_scene'] <= FALSE_TARGET
                               and summary['paid_false_per_scene'] <= PAID_FALSE_TARGET))
    feasible = [c for c in candidates if c['meets_false_report_targets']]
    if feasible:
        chosen = max(feasible, key=lambda c: c['simulation']['mean_score'])
    else:
        chosen = min(candidates, key=lambda c: (c['simulation']['paid_false_per_scene'], c['simulation']['false_per_scene'],
                                                -c['simulation']['mean_score']))
    return chosen, candidates


def pool_map(jobs, workers, label):
    records = []
    os.environ['OMP_NUM_THREADS'] = '1'   # one row per prediction: worker threads only wait on each other
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn')) as pool:
        for count, record in enumerate(pool.map(scene_job, jobs), 1):
            records.append(record)
            if count % 8 == 0 or count == len(jobs):
                print(f'{label} {count}/{len(jobs)}', flush=True)
    return records


def run(data, inputs, small, output, workers, grid_on_validation):
    data, inputs, small, output = Path(data), Path(inputs), Path(small), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    entries = json.loads((data/'manifest.json').read_text())['entries']
    protocol_small = json.loads((small/'protocol.json').read_text())
    cohort = str(max(protocol_small['sizes']))
    models = small/f'scenes-{int(cohort):03}'
    validation = [e for e in entries if e['split'] == 'validation']
    if [e['id'] for e in validation] != protocol_small['validation_ids']:
        raise ValueError('validation scenes differ from the small-scale model comparison')
    tuning = tuning_scenes(entries, set(protocol_small['training_ids'][cohort]))
    protocol = dict(version=VERSION, code={'baseline_comparison.py': benchmark.digest(__file__),
                                           'baseline conditions.py': benchmark.digest(AGENT/'core/conditions.py'),
                                           'baseline astro.py': benchmark.digest(AGENT/'core/astro.py')} | benchmark.sources(),
                    manifest_sha256=benchmark.digest(data/'manifest.json'),
                    models={kind: benchmark.digest(models/kind/'classifier.joblib') for kind in KINDS},
                    training_scenes=int(cohort), validation_ids=[e['id'] for e in validation],
                    threshold_selection_ids=[e['id'] for e in tuning], threshold_grid=THRESHOLDS,
                    false_report_targets=dict(per_scene=FALSE_TARGET, paid_per_scene=PAID_FALSE_TARGET),
                    baseline_variants=VARIANTS, model_probability='uncalibrated, as trained',
                    model_report_rule='status ready and three new usable exposures; free / paid probability thresholds',
                    baseline_belief='suspect and e < 0.78 e_ref', final_or_external_test_accessed=False,
                    source='formal public A-D inputs + self-generated weather, events and fixed schedules')
    protocol = json.loads(json.dumps(protocol))   # tuples become lists, as in the saved copy
    marker = output/'protocol.json'
    if marker.exists() and json.loads(marker.read_text()) != protocol:
        raise ValueError('comparison code, models or scenes changed; use another output directory')
    save_json(marker, protocol)
    started = time.perf_counter()
    grid = {kind: THRESHOLDS for kind in KINDS}
    job = lambda entry, mode, pairs: (str(data), str(inputs), str(models), str(output), entry, mode, pairs)
    tuned = pool_map([job(e, 'tuning', grid) for e in tuning], workers, 'threshold selection')
    tuning_runs = [r for record in tuned for r in record['runs']]
    selection = {}
    for kind in KINDS:
        chosen, candidates = select_thresholds(tuning_runs, kind)
        selection[kind] = dict(chosen=chosen, candidates=candidates)
    save_json(output/'selection.json', dict(frozen=True, scenes=len(tuning),
                                            none=summarize([r for r in tuning_runs if r['model'] == 'none'])) | selection)
    frozen = {kind: [tuple(selection[kind]['chosen']['thresholds'])] for kind in KINDS}
    # The rules were not tuned on any of these scenes, so the selection scenes are a second,
    # independent set for them; the models' thresholds were chosen here.
    ruled = pool_map([job(e, 'selection-rules', {kind: [] for kind in KINDS}) for e in tuning], workers, 'selection scenes, rules')
    rule_runs = [r for record in ruled for r in record['runs']]
    second = {name: [r for r in rule_runs if r['model'] == name] for name in ['none', 'oracle']+list(VARIANTS)}
    second |= {kind: [r for r in tuning_runs if r['model'] == kind and r['thresholds'] == list(frozen[kind][0])] for kind in KINDS}
    selection_scenes = {name: summarize(runs) for name, runs in second.items()}
    for name, runs in second.items():
        if name != 'none':
            selection_scenes[name]['gain_vs_none'] = paired(runs, second['none'])
        if name in KINDS:
            selection_scenes[name]['gain_vs_baseline'] = paired(runs, second['baseline'])
    checked = [dict(e, check=i % 16 == 0) for i, e in enumerate(validation)]
    records = pool_map([job(e, 'validation', frozen) for e in checked], workers, 'validation')
    runs = [r for record in records for r in record['runs']]
    names = ['none', 'oracle']+[n+s for n in VARIANTS for s in ('', '+final')]+[k+s for k in KINDS for s in ('', '+final')]
    simulation = {name: summarize([r for r in runs if r['model'] == name]) for name in names}
    none = [r for r in runs if r['model'] == 'none']; oracle = [r for r in runs if r['model'] == 'oracle']
    for name in names[1:]:
        own = [r for r in runs if r['model'] == name]
        simulation[name]['gain_vs_none'] = paired(own, none)
        simulation[name]['share_of_oracle_gain'] = simulation[name]['gain_vs_none']['mean']/paired(oracle, none)['mean']
        if name not in ('oracle', 'baseline'):
            simulation[name]['gain_vs_baseline'] = paired(own, [r for r in runs if r['model'] == 'baseline'])
    result = dict(scenes=len(records), rows=sum(r['rows'] for r in records),
                  classification={name: rates(records, name) for name in records[0]['counts']},
                  classification_by_card={card: {name: rates([r for r in records if r['card'] == card], name, 200)
                                                 for name in records[0]['counts']} for card in 'abcd'},
                  simulation=simulation, thresholds={kind: frozen[kind][0] for kind in KINDS},
                  selection_scenes=selection_scenes,
                  simulation_by_card={card: {name: summarize([r for r in runs if r['model'] == name and r['card'] == card])
                                             for name in names} for card in 'abcd'})
    if grid_on_validation:
        # Hindsight only: thresholds picked on the very scenes they are scored on.
        hindsight = pool_map([job(e, 'validation-grid', grid) for e in validation], workers, 'validation grid')
        grid_runs = [r for record in hindsight for r in record['runs']]
        result['hindsight'] = {}
        for kind in KINDS:
            chosen, candidates = select_thresholds(grid_runs, kind)
            best = max(candidates, key=lambda c: c['simulation']['mean_score'])
            own = lambda c: [r for r in grid_runs if r['model'] == kind and r['thresholds'] == c['thresholds']]
            result['hindsight'][kind] = dict(
                within_targets=chosen | dict(gain_vs_none=paired(own(chosen), none)),
                best_score=best | dict(gain_vs_none=paired(own(best), none)), candidates=candidates)
    result['wall_seconds'] = time.perf_counter()-started
    save_json(output/'results.json', result)
    write_report(output, result, json.loads((output/'selection.json').read_text()))
    print(json.dumps({k: result[k] for k in ('scenes', 'rows', 'wall_seconds')}))
    return result


def write_report(output, result, selection):
    percent = lambda v: '无' if v is None else f'{v:.1%}'
    number = lambda v, digits=1: '无' if v is None else f'{v:.{digits}f}'
    label = {'always_no_fault': '始终判无故障', 'baseline': '基线规则（原样）', 'baseline_2h': '基线规则（参照窗口 2 小时）',
             'baseline:suspect': '基线规则（原样，任何怀疑）', 'baseline_2h:suspect': '基线规则（2 小时，任何怀疑）',
             'boosting': '提升树（40 场景）', 'logistic': '逻辑回归（40 场景）', 'none': '不申报', 'oracle': '真实状态理想参照'}
    name_of = lambda n: label.get(n.replace('+final', ''), n)+('＋季末免费申报' if n.endswith('+final') else '')
    lines = ['# 故障判断：基线规则与小规模模型的同场景比较', '',
             '来源：正式 A–D 公开输入＋自行生成的天气、事件和固定观测安排；不是正式比赛隐藏数据，也不是比赛成绩。',
             f'评测：原定 {result["scenes"]} 个验证场景，每场景两条固定观测过程；没有读取最终或外部测试。', '',
             '## 一、逐曝光判断（与已有模型表同口径）', '',
             f'{result["rows"]} 条可比较反馈，每个场景等权；申报安排与训练数据相同（分散过程不申报，集中过程固定四次）。',
             '基线的判断取它自己的内部状态：正在怀疑仪器，且估计效率低于下降前的 78%（它累计申报证据的条件）。', '',
             '|方法|判断正确比例|故障状态发现比例|判为故障中正确比例|正常状态误报比例|', '|---|---:|---:|---:|---:|']
    for name in ('always_no_fault', 'baseline', 'baseline:suspect', 'baseline_2h', 'baseline_2h:suspect', 'logistic', 'boosting'):
        r = result['classification'][name]
        lines.append(f'|{name_of(name)}|'+'|'.join(percent(r[k]) for k in ('accuracy', 'recall', 'precision', 'false_positive_rate'))+'|')
    lines += ['', '## 二、各自决定申报的闭环模拟', '',
              '同一批固定观测安排；申报只发生在两次曝光之间，报对后其后的反馈和得分按修复后重新计算。',
              f'模型的申报规则沿用原方案：每三次新的可比较曝光判断一次，免罚额度内概率 ≥ 免罚门槛、额度用完后 ≥ 扣分门槛才申报。',
              f'门槛在 {selection["scenes"]} 个未参与训练的训练集场景上选定后冻结（先满足每场景误报 ≤ {FALSE_TARGET}、扣分误报 ≤ {PAID_FALSE_TARGET}，再取平均得分最高）：'
              +'；'.join(f'{name_of(k)} {result["thresholds"][k][0]}/{result["thresholds"][k][1]}'
                         +('' if selection[k]['chosen']['meets_false_report_targets'] else '（没有组合满足误报目标）') for k in KINDS)+'。', '']
    header = ['|方法|比不申报多得分（95% 区间）|最差场景|报对/场景|误报/场景|扣分误报/场景|申报正确率|故障发现率|发现延迟（曝光数，中位/90%）|发现延迟（小时，中位）|',
              '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']

    def table(block):
        rows = list(header)
        for name, s in block.items():
            if name == 'none':
                continue
            g = s['gain_vs_none']
            rows.append(f'|{name_of(name)}|{g["mean"]:+.1f}（{g["ci95"][0]:+.1f} 至 {g["ci95"][1]:+.1f}）|{g["worst_scene"]:+.0f}|'
                        f'{s["correct_per_scene"]:.2f}|{s["false_per_scene"]:.2f}|{s["paid_false_per_scene"]:.2f}|{percent(s["report_precision"])}|'
                        f'{percent(s["eligible_event_recall"])}|{number(s["delay_exposures"]["p50"], 0)}/{number(s["delay_exposures"]["p90"], 0)}|{number(s["delay_hours"]["p50"])}|')
        return rows

    lines += table(result['simulation'])
    none = result['simulation']['none']
    lines += ['', f'不申报的平均得分为 {none["mean_score"]:.1f}；可评估的故障 {none["eligible_events"]} 个（共 {none["all_events"]} 个，'
              '至少有三次可比较曝光才算可评估）。得分是每场景两条过程的平均，再对场景平均。', '',
              '模型与基线（原样）的配对差异（每场景得分，模型减基线）：'
              +'；'.join(f'{name_of(k)} {result["simulation"][k]["gain_vs_baseline"]["mean"]:+.1f}'
                         f'（{result["simulation"][k]["gain_vs_baseline"]["ci95"][0]:+.1f} 至 {result["simulation"][k]["gain_vs_baseline"]["ci95"][1]:+.1f}）'
                         for k in KINDS)+'。']
    second = result['selection_scenes']
    lines += ['', '### 另一批 64 个场景（选门槛用的那批）', '',
              '规则没有在这批场景上调过，对规则来说是第二批独立场景；模型的门槛就是在这批上选的，模型一行偏乐观。', '']
    lines += table(second)
    lines += ['', '模型与基线（原样）的配对差异：'
              +'；'.join(f'{name_of(k)} {second[k]["gain_vs_baseline"]["mean"]:+.1f}'
                         f'（{second[k]["gain_vs_baseline"]["ci95"][0]:+.1f} 至 {second[k]["gain_vs_baseline"]["ci95"][1]:+.1f}）' for k in KINDS)+'。']
    if 'hindsight' in result:
        lines += ['', '事后参照（门槛直接在这 64 个验证场景上挑，偏乐观，不能当作成绩）：']
        for kind, h in result['hindsight'].items():
            for key, text in (('within_targets', '满足误报目标中最高'), ('best_score', '不设误报目标的最高')):
                c = h[key]; s = c['simulation']
                if key == 'within_targets' and not c['meets_false_report_targets']:
                    text = '没有组合满足误报目标，取扣分误报最少的'
                lines.append(f'- {name_of(kind)}，{text}：门槛 {c["thresholds"][0]}/{c["thresholds"][1]}，比不申报 {c["gain_vs_none"]["mean"]:+.1f}，'
                             f'误报 {s["false_per_scene"]:.2f}/场景，扣分误报 {s["paid_false_per_scene"]:.2f}/场景，故障发现率 {percent(s["eligible_event_recall"])}。')
    lines += ['', '## 限制', '',
              '- 观测安排是固定的：每夜 8 次、间隔 60–80 分钟、程序按 DARK/BRIGHT/BACKUP 轮换、时长随机。基线在自己的比赛过程里连续观测，'
              '并用亮目标信标、DARK 探测和缩短曝光来读取仪器状态；这些手段在固定安排里都用不上。',
              '- 基线“原样”版的同夜突降判断只和一小时内的读数比，而这里相邻曝光间隔超过一小时，这条规则实际不生效；'
              '“2 小时”版只把这一个常数放宽，事先定好、没有按结果调整。',
              '- 模型只用 40 个训练场景，概率未校准；门槛选择用了另外 64 个训练集场景，没有碰校准、最终或外部测试。',
              '- 基线估计“还能拿多少分”时用固定安排里剩余的曝光秒数代替它原本用的剩余夜间时间；D 卡的数据撤销没有同步到这项估计。',
              '- 故障数量、强度和发生方式来自自行生成的配置，结论只适用于这些数据和这种观测安排。']
    (Path(output)/'REPORT.md').write_text('\n'.join(lines)+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='基线规则与小规模故障模型的同场景比较；只读训练与验证场景')
    parser.add_argument('--data', type=Path, default=Path('artifacts/instrument_faults'))
    parser.add_argument('--inputs', type=Path, default=Path('artifacts/instrument_faults-feedback'))
    parser.add_argument('--models', type=Path, default=Path('artifacts/instrument_faults-smallscale-4cpu'))
    parser.add_argument('--output', type=Path, default=Path('artifacts/instrument_faults-baseline-comparison'))
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--skip-validation-grid', action='store_true')
    args = parser.parse_args()
    run(args.data, args.inputs, args.models, args.output, args.workers, not args.skip_validation_grid)
