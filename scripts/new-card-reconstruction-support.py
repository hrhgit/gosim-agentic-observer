"""New-card capture adapters and transparent numeric-condition reconstruction."""
import bisect
import collections
import csv
from datetime import datetime,timedelta
import importlib.util
import json
import math
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agents/condition-probe-v1/project'))
from astro import Site,unit_vector

TERRAIN_HEIGHTS={'a':{'SE':32,'SW':37},'b':{'NE':32,'S':34,'NW':37},'c':{'N':33,'NE':36,'NW':38,'W':37,'SW':33,'S':35},'d':{'W':34,'SW':35,'SE':32,'S':35,'NW':33,'NE':36}}
# Manually transcribed from captured handovers, with exact source request IDs
# retained below. These are claims in messages, not organizer truth access.
CLOSURES={'a':[], 'b':[], 'c':[
 {'kind':'night_range','start_date':'2027-01-25','end_date_exclusive':'2027-02-06','reason_keyword':'1/25'},
 {'kind':'night_range','start_date':'2027-02-19','end_date_exclusive':'2027-03-03','reason_keyword':'2/19'},
 {'kind':'night_range','start_date':'2027-03-14','end_date_exclusive':'2027-03-26','reason_keyword':'３／１４'},
 {'kind':'local_interval','start':'2026-12-19T22:00:00','end':'2026-12-20T00:30:00','reason_keyword':'１２／１９'}],
 'd':[
 {'kind':'local_interval','start':'2027-01-06T23:15:00','end':'2027-01-07T01:45:00','reason_keyword':'1/6'},
 {'kind':'local_interval','start':'2027-05-07T21:45:00','end':'2027-05-08T01:45:00','reason_keyword':'５／７'},
 {'kind':'local_interval','start':'2027-09-21T22:45:00','end':'2027-09-22T01:15:00','reason_keyword':'9/21'},
 {'kind':'local_interval','start':'2027-12-13T23:15:00','end':'2027-12-14T01:30:00','reason_keyword':'12/13'}]}


def load_capture(root,repair_root,card):
    directory=next(root.glob('*-'+card+'1-v5'))
    inputs=[json.loads(line) for line in (directory/'captured.jsonl').read_text().splitlines()]
    if json.loads((directory/'score_report.json').read_text())['termination']['reason']!='survey_complete':raise ValueError('New reconstruction requires full survey capture')
    messages=[m for record in inputs for m in record['message'].get('payload',{}).get('new_messages',[])]
    original=next(repair_root.glob('*-'+card+'1-v5'))
    normal=[json.loads(line) for line in (original/'received-public-messages.jsonl').read_text().splitlines()]
    spec=importlib.util.spec_from_file_location('feedback_support',ROOT/'scripts/analyze-formal-feedback.py');feedback=importlib.util.module_from_spec(spec);spec.loader.exec_module(feedback)
    episodes=feedback.notice_episodes([m for m in messages if m['record_type']=='bulletin'])
    timeline=sorted([{'event_kind':kind,**episode} for kind,items in episodes.items() for episode in items],key=lambda row:row['first_public_slot_utc'])
    loss=[m for m in normal if m['record_type']=='state_resync'] or [m for m in messages if m['record_type']=='state_resync']
    observed={'public_event_timeline':timeline,'reports':[m for m in normal if m['record_type']=='report_result'],'state_resync':loss}
    return directory,observed,messages


def calibrate(card,directory,weather,events,cfg,bundle):
    slots={r['slot_id']:r for r in weather}
    ordered=sorted(weather,key=lambda r:r['timestamp_utc'])
    parse=lambda s:datetime.fromisoformat(s.replace('Z','+00:00'))
    starts=[parse(r['timestamp_utc']) for r in ordered]
    handovers=[m for record in map(json.loads,(directory/'captured.jsonl').read_text().splitlines()) for m in record['message'].get('payload',{}).get('new_messages',[]) if m.get('record_type')=='observation_request' and m['completion_reward']==0]
    closure_evidence=[];closed=set()
    for closure in CLOSURES[card]:
        source=next(m for m in handovers if closure['reason_keyword'] in m['reason'])
        applied=[]
        for row in weather:
            if closure['kind']=='night_range':
                date=row['night_id'][1:5]+'-'+row['night_id'][5:7]+'-'+row['night_id'][7:9]
                covered=closure['start_date']<=date<closure['end_date_exclusive']
            else:
                moment=parse(row['timestamp_utc']);local=moment+timedelta(hours=cfg['site']['utc_offset_hours'])
                left=datetime.fromisoformat(closure['start']).replace(tzinfo=moment.tzinfo);right=datetime.fromisoformat(closure['end']).replace(tzinfo=moment.tzinfo)
                covered=left<=local<right
            if covered:
                applied.append(row['slot_id']);closed.add(row['slot_id'])
        closure_evidence.append({**closure,'source_request_id':source['request_id'],'source_issued_at_utc':source['issued_at_utc'],'source_text':source['reason'],'applied_slots':len(applied),'timing_assumption':'short intervals classified by slot start; closure boundaries inside a slot are approximated'})
    # Fit only even-index exposures, reserving odd-index exposures for a later
    # interpolation check. Neighboring targets in one exposure are one sample.
    init=json.loads((directory/'initialize.json').read_text())['message']['payload']
    site=Site(init['site']['latitude_deg'],init['site']['longitude_deg'],init['site']['minimum_altitude_deg'],init['scoring']['airmass_exponent'],init['scoring'].get('lunar_model',{}))
    with (bundle/'public/targets.csv').open() as stream:targets={r['target_id']:r for r in csv.DictReader(stream)}
    samples=collections.defaultdict(list);excluded=collections.Counter()
    with (directory/'probe-measurements.csv').open() as stream:
        for m in csv.DictReader(stream):
            if m['valid']!='true' or int(m['observe_index'])%2:continue
            if float(m['combined_condition'])<=0:excluded['zero measurement']+=1;continue
            moment=parse(m['start_utc']);idx=bisect.bisect_right(starts,moment)-1
            if idx<0:continue
            row=ordered[idx]
            if row['is_observable']!='true' or row['slot_id'] in closed:excluded['predicted closed']+=1;continue
            midpoint=moment+timedelta(seconds=30);target=targets[m['target_id']]
            alt,az=site.altaz(unit_vector(float(target['ra_deg']),float(target['dec_deg'])),midpoint.timestamp())
            directional=1.0;blocked=False
            for e in events:
                if e.scope_type!='HORIZON_SECTOR' or not e.actual_start_utc<=midpoint<e.actual_end_utc:continue
                inside=(e.azimuth_start_deg<=az<=e.azimuth_end_deg) if e.azimuth_start_deg<=e.azimuth_end_deg else (az>=e.azimuth_start_deg or az<=e.azimuth_end_deg)
                if not inside or not e.min_altitude_deg<=alt<=e.max_altitude_deg:continue
                if e.force_close or e.zero_score:blocked=True;break
                directional*=e.instrument_efficiency_multiplier*e.transparency_multiplier*e.sky_quality_multiplier/e.seeing_multiplier
            if blocked:excluded['assumed directional obstruction']+=1;continue
            predicted=float(row['instrument_efficiency'])*float(row['transparency'])*float(row['sky_quality'])/float(row['seeing_arcsec'])*directional
            ratio=float(m['combined_condition'])/predicted
            samples[(row['night_id'],int(m['observe_index']))].append(ratio)
    per_night=collections.defaultdict(list)
    for (night,index),values in samples.items():per_night[night].append(statistics.median(values))
    factors={night:statistics.median(values) for night,values in per_night.items()}
    for row in weather:
        if row['slot_id'] in closed:
            row['is_observable']='false'
            for field in ('seeing_arcsec','transparency','sky_quality','instrument_efficiency'):row[field]=''
        elif row['is_observable']=='true' and row['night_id'] in factors:
            row['sky_quality']=f"{float(row['sky_quality'])*factors[row['night_id']]:.9f}"
    result={'source':'actual probe measurements','fit_exposure_selection':'even observe_index; exposure-level median then night median; odd exposures excluded from fit','fit_exposures':len(samples),'calibrated_nights':len(factors),'night_factors':factors,'exclusions':dict(excluded),'maintenance_closed_slots':len(closed),'effective_parameter':'sky_quality absorbs an unidentifiable combined scaling; not a recovered physical sky parameter','limitations':['directional sectors and fault efficiency remain assumed','uncalibrated nights retain seeded reference weather','only public-target directions were probed','this interpolates one recorded scenario, not independent future weather']}
    return weather,result,closure_evidence


def readme(audit):
    lines=['# 新正式卡 A1–D1(v5) 本地重建版','', '来源：探测采集批次 aec355e9 的四张新卡均完整到 survey_complete；此前正常参赛批次41bc72b2补充故障报告真假证据。公开公告、预测、全部请求（含完整交接文字、emoji、全角数字、中日文和高奖励请求）原样恢复，隐藏条件是自行推断/生成，不是官方真值。', '', '| 卡目录 | 对应新卡 | 公告 | 预测 | 请求 |','|---|---|---:|---:|---:|']
    for card,row in audit.items():lines.append(f"| cards/{card} | {card.upper()}1 | {row['bulletins_exact']} | {row['forecasts_exact']} | {row['requests_exact']} |")
    lines+=['', '本地裁判仍使用公开v4协议与评分公式；v5是场景版本名。目标、台址、仪器、计分、日历和天区沿用与新开局逐字段核实相同的正式公开输入。旧任务卡说明仅作原基础输入的存档，不冒充新卡官方说明。', '', '## 重建条件与依据','', '- 地形：从初始交接提取被纠正后的山脊高度，方位宽度暂按45度；高度是交接中的断言，宽度未公开。来源原文见 terrain-height-claims.json。','- 天气事件：恢复所有粗种类/方向的连续公告段；一个段暂按一个事件，精确边界及范围未知，不能宣称还原了隐藏事件总数。薄云/霾基准严重度取0.25，其余0.75。','- 数值：从探测数据扣除目标高度和月亮影响，使用偶数曝光的综合条件值校准每夜尺度；邻近目标先合并成曝光级样本。奇数曝光不参与拟合，用于另行检查。有效 sky_quality 是吸收综合差异的参数，不是恢复的真实天空质量。缺少有效样本的夜晚继续使用固定种子的参考天气。','- 停机：根据交接明示日期恢复C1三段长期停机及一次短维护、D1四次短维护；全角/中文时间由人工转录并保留完整来源，短时段按900秒槽起点近似，槽内边界仍有误差。','- 故障：已成功修复所证明的数量与真假报告时序来自正常运行记录；起点和效率仍为假设。D1正常运行仅有前三个修复的证据，未检出或后期故障仍可能未恢复。探测版不修复故障，不能直接拿其总分作基线。','- D1：恢复40%数据丢失上限与收到的比例；触发时间用正常运行的通知时刻作近似，窗口按本次行动数量重新计算。指向偏差尚未知，暂取0。', '', '事件证据、维护来源与夜尺度在每卡 generator/。这是固定场景的本地响应/对比材料，不能保证线上同分或用拟合场景声称泛化到未知天气。', '', '## 使用','', '```sh', 'python3.12 agents/claude-baseline-v1/tools/run_eval.py --eval-set formal-v5-new-four-reconstructed-v1 --tag new-four-v5', '```','', '默认本地评测仍为旧A–D时间对齐套件；此处显式选择新套件。没有再次提交比赛或更改最终作品。重建命令：', '', '```sh','python3 scripts/reconstruct-formal-timeline.py --new-capture artifacts/formal-card-refresh-2026-10-05/probe-new-four --out evaluation_assets/formal-v5-new-four-reconstructed-v1-copy','```','']
    return '\n'.join(lines)
