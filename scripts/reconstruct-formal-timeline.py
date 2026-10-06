#!/usr/bin/env python3
"""Reconstruct public formal timeline; generate explicitly assumed hidden conditions."""
import argparse
import copy
import csv
from datetime import datetime, timedelta
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from challenge import v4_weather_simulator as sim
from challenge.v4_workflow import V4Workflow

MAP = {'rain': 'rainy', 'overcast': 'cloudy', 'haze': 'smoggy', 'cold_snap': 'cold_wave', 'storm': 'tornado', 'rocket_launch': 'rocket_launch', 'earthquake': 'earthquake', 'terrain_obstruction': 'terrain_obstruction'}
PUBLIC = ['config/v4_fiber_config.json', 'config/v4_score_config.json', 'public/footprint.csv', 'public/targets.csv', 'public/taskcard.en.md', 'public/taskcard.zh.md', 'public/v4_night_calendar.csv']


def dt(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00'))


def write_csv(path, columns, rows):
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parsed', type=Path, default=ROOT/'artifacts/formal-feedback-2026-10-05/selective-capture-v2-evaluation/parsed')
    parser.add_argument('--new-capture', type=Path, help='full processed A1-D1 probe captures')
    parser.add_argument('--repair-evidence', type=Path, default=ROOT/'artifacts/formal-card-refresh-2026-10-05/captured-runs')
    parser.add_argument('--out', type=Path, default=ROOT/'evaluation_assets/formal-v4-observed-timeline-v4')
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit('Refusing to overwrite existing output')
    args.out.mkdir(parents=True)
    support = None
    if args.new_capture:
        spec = importlib.util.spec_from_file_location('new_support', ROOT/'scripts/new-card-reconstruction-support.py')
        support = importlib.util.module_from_spec(spec); spec.loader.exec_module(support)
    audit = {}
    for index, card in enumerate('abcd'):
        source = ROOT/'evaluation_assets/formal-v4-feedback-self-generated-v3/nominal/cards'/card
        bundle = args.out/'cards'/card
        shutil.copytree(source, bundle)
        shutil.rmtree(bundle/'generator')
        (bundle/'generator').mkdir()
        if support:
            capture_dir, observed, messages = support.load_capture(args.new_capture, args.repair_evidence, card)
        else:
            capture_dir = args.parsed.parent/next(p.name for p in args.parsed.parent.glob('*-'+card))
            observed = json.loads((args.parsed/card/'analysis.json').read_text())
            messages = [json.loads(line) for line in (args.parsed/card/'public-messages.jsonl').read_text().splitlines()]
        cfg = json.loads((source/'generator/weather_config.json').read_text())
        cfg['seed'] = 9105001 + index
        cfg['background_closure'].update(start_probability_per_open_slot=0, seasonal_probability_amplitude=0)
        # A random unannounced closure cannot be inferred from a message. Avoid adding
        # one to this nominal hypothesis; observed unexplained closures stay unknown.
        nights, slots = sim.build_nights(cfg)
        by_time = {sim.format_utc(slot.timestamp_utc): slot for slot in slots}
        night_order = {night.night_id: i for i, night in enumerate(nights)}
        events, effects, evidence = [], [], []
        for episode in observed['public_event_timeline']:
            kind = MAP[episode['event_kind']]
            first = by_time[episode['first_public_slot_utc']]
            last = by_time[episode['last_public_slot_utc']]
            event_id = f'RC{len(events)+1:04d}'
            scope = 'ALL' if episode['direction'] == 'ALL' else 'HORIZON_SECTOR'
            # Compass direction gives center bin only, not width or altitude bounds.
            az_start = az_end = min_alt = max_alt = None
            if scope != 'ALL':
                center = sim.DIRECTION_CODES.index(episode['direction']) * 45.0
                width = 45.0 if kind == 'terrain_obstruction' else 60.0
                az_start, az_end = (center-width/2)%360, (center+width/2)%360
                min_alt, max_alt = 0.0, 60.0
            multipliers = [1.0, 1.0, 1.0, 1.0]
            force_close = kind == 'rocket_launch'
            severity = .25 if support and kind in ('cloudy', 'smoggy') else .75
            magnitude = None
            end = last.end_utc
            if kind == 'terrain_obstruction':
                end = slots[-1].end_utc
                max_alt = float(support.TERRAIN_HEIGHTS[card][episode['direction']])
            if kind in sim.WEATHER_CONDITIONS:
                definition = cfg['weather_events']['conditions'][kind]
                multipliers[:3] = [sim._scaled_multiplier(float(definition[field]), severity) for field in ('seeing_multiplier', 'transparency_multiplier', 'sky_quality_multiplier')]
                force_close = bool(definition['force_close'])
            elif kind == 'earthquake':
                # Duration constrains a combination of magnitude, decay and cutoff.
                # Hold reference decay/cutoff fixed; infer the midpoint compatible
                # initial degradation, not an independently measured magnitude.
                span = night_order[last.night_id]-night_order[first.night_id]+1
                quake_cfg = copy.deepcopy(cfg)
                minimum_decay = (span-.5)/math.log(float(cfg['earthquake']['max_degradation'])/float(cfg['earthquake']['negligible_degradation']))
                decay = max(float(cfg['earthquake']['decay_nights']), minimum_decay + .1)
                quake_cfg['earthquake']['decay_nights'] = decay
                initial = float(cfg['earthquake']['negligible_degradation'])*math.exp((span-.5)/decay)
                magnitude = float(cfg['earthquake']['reference_magnitude'])+math.log(initial)/float(cfg['earthquake']['impact_coefficient'])
                for k in range(span):
                    degradation = sim.earthquake_night_degradation(initial, k, quake_cfg)
                    mult = sim._earthquake_multipliers(degradation, cfg)
                    effects.append(sim.EarthquakeEffect(event_id, nights[night_order[first.night_id]+k].night_id, magnitude, degradation, *mult))
                end = first.end_utc  # shock row; decaying effects live in separate table
            events.append(sim.V4Event(event_id, kind, first.timestamp_utc, end, scope, az_start, az_end, min_alt, max_alt, magnitude, severity, force_close, kind == 'terrain_obstruction', *multipliers))
            evidence.append({'event_id': event_id, 'origin': 'public-episode-derived hidden-condition hypothesis', 'observed_episode': episode, 'assumed': {'boundaries': 'first slot start and last slot end; hidden boundaries may differ', 'one_event_per_episode': True, 'severity': severity, 'sector_width_deg': width if scope != 'ALL' else None, 'sector_max_alt_deg': max_alt, 'earthquake_magnitude': magnitude, 'earthquake_decay_nights': decay if magnitude is not None else None}})
        repairs = sorted([m for m in observed['reports'] if m['correct']], key=lambda m:m['issued_at_utc'])
        fault_starts = []
        for report in repairs:
            before = [slot for slot in slots if slot.timestamp_utc < dt(report['issued_at_utc'])]
            report_night = before[-1].night_id
            onset = next(slot.timestamp_utc for slot in before if slot.night_id == report_night)
            # False reports bound onset from below: successful report's same-night
            # onset cannot precede a false report after the preceding successful repair.
            previous_repair = repairs[len(fault_starts)-1]['issued_at_utc'] if fault_starts else ''
            false_before = [dt(m['issued_at_utc']) for m in observed['reports'] if not m['correct'] and previous_repair < m['issued_at_utc'] < report['issued_at_utc']]
            if false_before:
                onset = max(onset, max(false_before)+timedelta(microseconds=1))
            fault_starts.append(onset)
        for position, (onset, report) in enumerate(zip(fault_starts, repairs)):
            event_id = f'RC{len(events)+1:04d}'
            end = fault_starts[position+1] if position+1 < len(fault_starts) else slots[-1].end_utc
            events.append(sim.V4Event(event_id, 'instrument_fault', onset, end, 'ALL', None, None, None, None, None, 1.0, False, False, 1.0, 1.0, 1.0, .55))
            evidence.append({'event_id': event_id, 'origin': 'successful-repair lower-bound hypothesis', 'repair_observed_utc': report['issued_at_utc'], 'assumed': {'onset': 'start of repair night, bounded by prior false reports', 'instrument_efficiency_multiplier': .55, 'count': 'successful repairs only; undetected faults unknown'}})
        weather_rows = sim.generate_weather(cfg, nights, slots, events, effects)
        calibration = closure_evidence = None
        if support:
            weather_rows, calibration, closure_evidence = support.calibrate(card, capture_dir, weather_rows, events, cfg, bundle)
        write_csv(bundle/'truth/v4_events.csv', sim.V4_EVENT_COLUMNS, [event.csv_row() for event in sorted(events, key=lambda e:(e.actual_start_utc,e.event_id))])
        write_csv(bundle/'truth/v4_earthquake_effects.csv', sim.V4_EARTHQUAKE_EFFECT_COLUMNS, [effect.csv_row() for effect in effects])
        write_csv(bundle/'truth/v4_weather_truth.csv', list(weather_rows[0]), weather_rows)
        for kind, relative in [('bulletin','public/v4_bulletins.jsonl'),('forecast','public/v4_forecasts.jsonl'),('observation_request','truth/v4_observation_requests.jsonl')]:
            (bundle/relative).write_text(''.join(json.dumps(m,ensure_ascii=False)+'\n' for m in messages if m['record_type']==kind))
        stress_rows=[]
        for loss in observed['state_resync']:
            window=loss['invalidated_window']
            stress_rows.append({'event_id':loss['trigger_event_id'], 'event_type':'data_loss','trigger_mode':'fixed_date','trigger_ref':loss['issued_at_utc'],'window_start_fraction':window['window_start_fraction'],'window_end_fraction':window['window_end_fraction'],'window_max_fraction':window['window_max_fraction'],'alt_offset_rad':'','az_offset_rad':''})
        if card == 'd':
            stress_rows.append({'event_id':'RCST0002','event_type':'pointing_offset','trigger_mode':'at_survey_start','trigger_ref':'','window_start_fraction':'','window_end_fraction':'','window_max_fraction':'','alt_offset_rad':0,'az_offset_rad':0})
            write_csv(bundle/'truth/v4_stress_events.csv',sim.V4_STRESS_EVENT_COLUMNS,stress_rows)
        scenario=json.loads((bundle/'config/v4_scenario.json').read_text())
        scenario['name']=f'formal-{card}1-v5-reconstructed-probe-calibrated' if support else f'formal-{card}-observed-timeline-assumed-conditions-v4'
        scenario['task_card'].update(card_id=(f'{card.upper()}1-RECONSTRUCTED-ASSUMED' if support else f'{card.upper()}-RECONSTRUCTED-ASSUMED'),phase='local-observed-timeline',scenario_slug=scenario['name'])
        write_json(bundle/'config/v4_scenario.json',scenario)
        write_json(bundle/'generator/weather_config.json',cfg)
        write_json(bundle/'generator/event-evidence.json',evidence)
        if support:
            write_json(bundle/'generator/measurement-calibration.json',calibration)
            write_json(bundle/'generator/maintenance-evidence.json',closure_evidence)
            write_json(bundle/'generator/terrain-height-claims.json',{'heights':support.TERRAIN_HEIGHTS[card], 'source_request_id':'V4RQ1001', 'source_text':next(m['reason'] for m in messages if m.get('request_id')=='V4RQ1001'), 'classification':'claims extracted from handover; azimuth width still assumed'})
        write_json(bundle/'generator/observed-state-resync.json',observed['state_resync'])
        # Independently regenerate announcements from hypothesised events. Compare
        # sets, because notice list ordering has no event meaning.
        derived=[json.loads(line) for line in sim.generate_bulletins(cfg,nights,slots,events,effects)]
        published=[m for m in messages if m['record_type']=='bulletin']
        canonical=lambda b:(b['slot_id'],b['issued_at_utc'],b['initial'],frozenset((n['event_kind'],n['direction']) for n in b['notices']))
        derived_by_slot = {b['slot_id']: b for b in derived}
        for right in published:
            left = derived_by_slot[right['slot_id']]
            if canonical(left) != canonical(right):
                raise ValueError(f'Announcement mismatch: {left} versus {right}')
        missing_public_slots = len(derived) - len(published)
        workflow=V4Workflow(bundle)
        # Same report times must yield the same true/false report outcomes.
        from challenge.v4_scorer import WeatherTruth
        truth=WeatherTruth(workflow.scenario.slots,[e for e in workflow.scenario.events if e.event_type=='instrument_fault'])
        for report in observed['reports']:
            assert truth.repair_fault(dt(report['issued_at_utc'])) == report['correct']
        hashes={relative:hashlib.sha256((bundle/relative).read_bytes()).hexdigest() for relative in PUBLIC}
        assert all(hashes[relative]==hashlib.sha256((ROOT/'cards'/card/relative).read_bytes()).hexdigest() for relative in PUBLIC)
        metadata={'classification':'reconstructed contestant-visible formal timeline plus SELF-GENERATED hidden-condition hypotheses; NOT official hidden truth','source_evaluation':('aec355e9-1315-4768-bfea-8063fa8c786d' if support else 'ed5ec77b-ac2d-450d-90df-95ff22477b75'),'source_capture_sha256':hashlib.sha256((capture_dir/'captured.jsonl').read_bytes()).hexdigest(),'public_inputs_sha256':hashes,'seed':cfg['seed'],'exact_public_records':['bulletins','forecasts','observation_requests'],'assumptions':['one event per contiguous kind/direction episode','episode slot-edge timing proxy','reference weather, new seed, no unannounced background closures','severity .75; directional width60deg maxalt60deg','quake decay at least reference4nights, increased if required by published duration; magnitude is conditional on this assumption','fault .55 efficiency and repair-night onset; no undetected faults','D unknown pointing offset set to zero; data-loss notice timestamp used as trigger proxy'], 'data_loss_semantics':'Fractions replayed; action-index window and invalidated count change with agent actions. Observed notice time may be later than actual trigger.', 'public_bulletin_regeneration_verified':True,'official_report_outcomes_verified':True}
        if support:
            metadata['assumptions']=['one event per contiguous public episode; exact boundaries and sectors unknown','thin cloud/haze severity .25, other severity .75 assumed','terrain height claims from handover; width45deg assumed','fault repair-night onset/efficiency .55 inferred from prior normal-run reports; D is a lower bound','reference quake decay increased if required by observed duration','D pointing offset0 and data-loss notification-time trigger proxy']
            metadata['classification']='reconstructed A1-D1 received messages plus SELF-GENERATED inferred conditions; NOT official hidden truth'
            metadata['source_repair_evaluation']='41bc72b2-775e-4197-8ba0-05151e3223c5'
            metadata['calibration']=calibration
        write_json(bundle/'self_generated_metadata.json',metadata)
        (bundle/'truth/README.md').write_text('# 本地推测条件\n\n事件范围、强度、故障起点和天气为自行补齐的假设，不是官方隐藏数据。公告、预测和请求来自已收到的正式反馈。公开目标表保持原样。智能体不可读取本目录。\n')
        audit[card]={'valid_bundle':True,'bulletins_exact':len(published),'forecasts_exact':sum(m['record_type']=='forecast' for m in messages),'requests_exact':sum(m['record_type']=='observation_request' for m in messages),'public_hashes_verified':len(hashes),'report_outcomes_match':True,'hypothesized_events':len(events),'observed_timeline_only':True,'slots_without_received_bulletin':missing_public_slots}
        print(card,audit[card],flush=True)
    write_json(args.out/'manifest.json',audit)
    (args.out/'README.md').write_text('''# 正式卡线上时间对齐版 v4\n\n本套数据重建已经收到的正式公告、预测和请求，时间与内容原样保留；隐藏天气与事件参数由本地假设补齐，**不是官方隐藏数据，不保证本地分数等于线上**。原公开输入和旧评测集均未修改。\n\n每卡 `generator/event-evidence.json` 区分观察到的时间段与推测的范围/强度/故障起点，`self_generated_metadata.json` 列出全部假设和来源。事件衍生公告已独立验证与收到的正式公告逐槽一致；没有收到公告的尾部槽位不冒充已知，故障报告真假与已记录结果一致。\n\n地震幅度由公告持续夜数在假定衰减规则下推算；若参考衰减太短，增加到能解释公告持续夜数的值，不能当作测得震级；方向扇区宽度假定60度、最高高度60度，严重程度0.75。故障只恢复成功修复所证明的数量下限，开始时间假定为修复夜开端，效率0.55。D 指向偏差暂取0，数据丢失按已收到通知时间和比例重建，换行动后失效窗口会改变。后台天气使用新固定种子，不拟合完整天况；不额外加入随机关闭。\n\n```sh\npython3.12 examples/_local/runner/run_local.py --card evaluation_assets/formal-v4-observed-timeline-v4/cards/a --agent "python3.12 agent.py" --agent-cwd agents/claude-baseline-v1/agent --out artifacts/formal-feedback-2026-10-05/local-timeline-v4/a --quiet\n```\n\n本地工具默认使用本套；旧v3可通过 `--eval-set formal-v4-feedback-self-generated-v3/nominal` 显式选择。构建命令：`python3 scripts/reconstruct-formal-timeline.py --out evaluation_assets/formal-v4-observed-timeline-v4-copy`。\n''')

    if support:
        (args.out/'README.md').write_text(support.readme(audit))

if __name__=='__main__':main()
