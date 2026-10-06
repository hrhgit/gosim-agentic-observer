#!/usr/bin/env python3
"""Validate downloaded probe result archive and estimate combined conditions."""
import argparse
import collections
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def load(name,file):
    spec=importlib.util.spec_from_file_location(name,file)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    extractor=load('capture_extractor',ROOT/'agents/claude-baseline-v1/tools/extract_capture.py')
    analyzer=load('probe_analyzer',ROOT/'scripts/analyze-condition-probes.py')
    summaries={}
    canonical=lambda x:json.dumps(x,sort_keys=True,separators=(',',':'))
    with zipfile.ZipFile(args.archive) as archive:
        meta=json.loads(archive.read('evaluation.json'))
        (args.out/'evaluation.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
        folders=sorted({n.split('/')[0] for n in archive.namelist() if n.endswith('/score_report.json')})
        for folder in folders:
            destination=args.out/folder;destination.mkdir(exist_ok=True)
            for name in ('agent.log','actions.jsonl','decisions.csv','messages.jsonl','observations.csv','score_report.json','workflow_result.json'):
                (destination/name).write_bytes(archive.read(folder+'/'+name))
            report=json.loads((destination/'score_report.json').read_text())
            for filename,key in [('messages.jsonl','messages'),('decisions.csv','decisions'),('observations.csv','observations')]:
                assert hashlib.sha256((destination/filename).read_bytes()).hexdigest()==report['sha256'][key]
            output=io.StringIO();count=extractor.extract(io.StringIO((destination/'agent.log').read_text()),output)
            (destination/'captured.jsonl').write_text(output.getvalue())
            records=[json.loads(line) for line in output.getvalue().splitlines()]
            assert records[0]['message']['message_type']=='initialize' and records[-1]['message']['message_type']=='finish'
            init=records[0]['message']['payload'];(destination/'initialize.json').write_text(json.dumps(records[0],ensure_ascii=False,indent=2)+'\n')
            received=[m for row in records for m in row['message'].get('payload',{}).get('new_messages',[])]
            expected=[json.loads(line) for line in (destination/'messages.jsonl').read_text().splitlines()]
            missing=collections.Counter(map(canonical,expected))-collections.Counter(map(canonical,received))
            extra=collections.Counter(map(canonical,received))-collections.Counter(map(canonical,expected))
            card=report['scenario'];letter=card.removeprefix('v4-')[0]
            if letter not in 'abcd':raise ValueError('Not a formal A-D family')
            summary=analyzer.analyze(destination,ROOT/'cards'/letter/'public/targets.csv')
            summary.update({'scenario':card,'capture_records':count,'all_message_records_received':not missing and not extra,'missing_result_messages':[json.loads(k) for k in missing],'extra_capture_messages':sum(extra.values()),'delivered_counts':dict(collections.Counter(m['record_type'] for m in received)),'original_survey_end_utc':init['survey']['end_utc'],'last_received_now_utc':next(row['message']['payload']['now_utc'] for row in reversed(records) if row['message']['message_type']=='decision_request'),'run_fair_clock':json.loads((destination/'workflow_result.json').read_text()).get('fair_clock')})
            assert summary['full_survey_completed'] and not missing and not extra,summary
            summaries[card]=summary
            print(card,'complete','hits',summary['measured_target_hits'],'log bytes',summary['logged_bytes'],flush=True)
    (args.out/'capture-and-probe-audit.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2)+'\n')
    lines=['# 强度探测采集结果','', '专用测量版本，不以比赛总分评价。所有卡必须正常到 survey_complete，日志从开局到finish、记录顺序与校验通过，结果包公开消息与收到的消息逐条核对。', '', '| 卡 | 接收记录 | 目标测量数 | 可比较对数 | 日志MiB | 完整结束 |','|---|---:|---:|---:|---:|---|']
    for card,r in summaries.items():lines.append(f"| {card} | {r['capture_records']} | {r['measured_target_hits']} | {r['short_gap_comparable_pairs']} | {r['logged_bytes']/2**20:.3f} | 是 |")
    lines+=['', '每卡 probe-measurements.csv 给出原始质量和扣除月亮/高度后的综合条件值；probe-condition-by-night.csv 按夜和粗方向汇总，以每次曝光而非每个邻近目标计样本；probe-pairs.json 保留两次近邻时间观测的比较。', '', '估计的是仪器效率×透明度×天空质量/成像模糊程度的综合影响，不能唯一拆成各项或当作某一事件的真实强度。参考方向也可能受天气影响。缺失命中有可能来自指向/光纤位置问题；零质量有多种原因。此版本不修复仪器故障，条件状态与正常参赛版本有差异。', '']
    (args.out/'README.md').write_text('\n'.join(lines))

if __name__=='__main__':main()
