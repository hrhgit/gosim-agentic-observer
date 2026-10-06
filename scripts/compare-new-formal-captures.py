#!/usr/bin/env python3
"""Compare old/new formal cards from the same contestant evaluation, using captures."""
import argparse
import collections
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('feedback',ROOT/'scripts/analyze-formal-feedback.py')
feedback=importlib.util.module_from_spec(spec);spec.loader.exec_module(feedback)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',type=Path,default=ROOT/'artifacts/formal-card-refresh-2026-10-05')
    args=parser.parse_args();base=args.base;cards={}
    canonical=lambda value:json.dumps(value,sort_keys=True,separators=(',',':'))
    with zipfile.ZipFile(base/'new-eight-card-capture.zip') as archive:
        for directory in sorted((base/'captured-runs').iterdir()):
            if not (directory/'captured.jsonl').exists():continue
            records=[json.loads(line) for line in (directory/'captured.jsonl').read_text().splitlines()]
            inputs=[row['message'] for row in records];init=inputs[0]['payload']
            messages=[msg for row in inputs[1:] for msg in row.get('payload',{}).get('new_messages',[])]
            report=json.loads(archive.read(directory.name+'/score_report.json'))
            exported=[json.loads(line) for line in archive.read(directory.name+'/messages.jsonl').decode().splitlines()]
            for filename,key in [('messages.jsonl','messages'),('observations.csv','observations'),('decisions.csv','decisions')]:
                assert hashlib.sha256(archive.read(directory.name+'/'+filename)).hexdigest()==report['sha256'][key]
            missing=collections.Counter(map(canonical,exported))-collections.Counter(map(canonical,messages))
            extra=collections.Counter(map(canonical,messages))-collections.Counter(map(canonical,exported))
            req=[msg for msg in messages if msg['record_type']=='observation_request']
            bulletins=[msg for msg in messages if msg['record_type']=='bulletin']
            forecasts=[msg for msg in messages if msg['record_type']=='forecast']
            repairs=[msg for msg in messages if msg['record_type']=='report_result']
            episodes=feedback.notice_episodes(bulletins)
            handovers=[msg for msg in req if msg['completion_reward']==0]
            initial=next((msg for msg in bulletins if msg.get('initial')),None)
            requests=report['observation_requests']
            group=lambda predicate:{'issued':sum(predicate(q) for q in requests),'completed':sum(predicate(q) and q['completed'] for q in requests),'earned_reward':sum(q['reward'] for q in requests if predicate(q))}
            row={'scenario':report['scenario'],'score':report['total'],'components':report['components'],'counts':report['counts'],'termination':report['termination'],'log_bytes':(directory/'agent.log').stat().st_size,'captured_records':len(records),'capture_first_last':[records[0]['received_sequence'],records[-1]['received_sequence']],'capture_initialize_finish':inputs[0]['message_type']=='initialize' and inputs[-1]['message_type']=='finish','delivered_message_counts':dict(collections.Counter(msg['record_type'] for msg in messages)),'export_vs_delivered':{'missing':sum(missing.values()),'extra':sum(extra.values()),'not_delivered_records':[json.loads(k) for k in missing]},'initial_terrain_directions':[n['direction'] for n in (initial or {}).get('notices',[]) if n['event_kind']=='terrain_obstruction'],'forecast_horizon_hours':sorted(set((feedback.datetime.fromisoformat(msg['coverage_end_utc'].replace('Z','+00:00'))-feedback.datetime.fromisoformat(msg['coverage_start_utc'].replace('Z','+00:00'))).total_seconds()/3600 for msg in forecasts)),'event_episode_counts':{kind:len(items) for kind,items in episodes.items()},'correct_reports':sum(bool(msg['correct']) for msg in repairs),'reports':repairs,'request_groups':{'zero_reward_handover':group(lambda q:q['completion_reward']==0),'ordinary_reward_100':group(lambda q:q['completion_reward']==100),'higher_reward':group(lambda q:q['completion_reward']>100)},'handover_count_received':len(handovers),'state_resync':[{k:msg[k] for k in ('issued_at_utc','invalidated_window')} for msg in messages if msg['record_type']=='state_resync'],'last_simulation_time':next(row['payload'].get('now_utc') for row in reversed(inputs) if row.get('message_type')=='decision_request'),'initialize':init,'omitted_targets':records[0]['omitted']}
            cards[report['scenario']]=row
            (directory/'received-public-messages.jsonl').write_text(''.join(json.dumps(msg,ensure_ascii=False)+'\n' for msg in messages))
            (directory/'handover-messages.jsonl').write_text(''.join(json.dumps(msg,ensure_ascii=False)+'\n' for msg in handovers))
            (directory/'analysis.json').write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n')
            ordered=sorted([{'event_kind':kind,**episode} for kind,items in episodes.items() for episode in items],key=lambda q:q['first_public_slot_utc'])
            with (directory/'event-timeline.csv').open('w',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=['event_kind','direction','first_public_slot_utc','last_public_slot_utc','published_slots']);writer.writeheader();writer.writerows(ordered)
    comparison={}
    for letter in 'abcd':
        old,new=cards['v4-'+letter],cards['v4-'+letter+'1-v5']
        different={key:{'old':old['initialize'].get(key),'new':new['initialize'].get(key)} for key in old['initialize'].keys()|new['initialize'].keys() if old['initialize'].get(key)!=new['initialize'].get(key)}
        comparison[letter]={'old_score':old['score'],'new_score':new['score'],'score_delta':new['score']-old['score'],'score_delta_percent':(new['score']/old['score']-1)*100,'public_target_hash_same':old['omitted_targets']==new['omitted_targets'],'initialize_changed_fields':different,'old_public_episodes':old['event_episode_counts'],'new_public_episodes':new['event_episode_counts'],'new_request_groups':new['request_groups'],'new_termination':new['termination'],'new_initial_terrain_directions':new['initial_terrain_directions'],'old_forecast_horizon_hours':old['forecast_horizon_hours'],'new_forecast_horizon_hours':new['forecast_horizon_hours'],'old_correct_reports':old['correct_reports'],'new_correct_reports':new['correct_reports'],'new_last_simulation_time':new['last_simulation_time']}
    (base/'run-comparison.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2)+'\n')
    lines=['# 新四卡实际参赛采集比较','', '比较同一个选择性采集版本 292f4e6f 的同一批正式运行 41bc72b2（洛杉矶2026-10-05 14:52创建），旧 A–D 与新 A1–D1(v5)。数据来自 agent.log 解压及同次结果包；不是公开下载卡配置猜测。', '', '## 核心变化','', '开局的目标内容指纹、地点、日程、仪器、计分、天区均相同；变化是任务卡标识和机器速度校准值。v5 是场景版本名，消息协议仍是 participant-agent-protocol-v4。', '', '新卡加入了零奖励的自然语言交接消息，装在 observation_request.reason 中；它们包含山脊高度、局部天气/关闭时段、停机维护、故障线索、不同说话者互相纠正、中文与日文混合，以及无关闲聊。文字中的断言是收到的线索，尚未逐条与隐藏真值核实。', '', 'D1数据丢失窗口上限从15%增加到40%，本次使4533条观测失效（旧D为1196条）。新增高奖励紧急请求，每条300–500分，通常4选2或5选3；原有8选6/100分请求仍在。预测窗口从7天缩为2天；各卡开局新增永久地形遮挡公告。事件时间线和故障也改变。D1在模拟10月5日耗尽运行预算，因此它的雨、火箭等公告数量只覆盖收到的前期，不能与完整旧D直接当总事件数比较。', '', '| 卡 | 旧卡分数 | 新卡分数 | 零奖励交接数（裁判已发出） | 高奖励请求 完成/发出 | 正确故障报告 旧→新 |','|---|---:|---:|---:|---:|---:|']
    for c in 'abcd':
        old,new=cards['v4-'+c],cards['v4-'+c+'1-v5'];groups=new['request_groups'];high=groups['higher_reward']
        lines.append(f"| {c.upper()}→{c.upper()}1 | {old['score']:.2f} | {new['score']:.2f} | {groups['zero_reward_handover']['issued']} | {high['completed']}/{high['issued']} | {old['correct_reports']}→{new['correct_reports']} |")
    lines+=['', '## 影响与限制','', '当前基线解析请求的目标/奖励，并处理粗公告，但没有读取 reason 中的交接内容（core/state.py 与 agent.py 已核对）。因此会漏用更细的山脊高度、天气时间和故障线索。不能仅凭得分差把全部影响归因于没读文字：天气、地形和故障本身也变了。', '', 'C1 对应基线总分下降约27.7%，基础观测得分从26727.31降到17449.07，观测到的不同目标从28457降到20616；但请求奖励从600增加到1800、故障奖励从100增加到600。这提示主要损失出现在有效观测覆盖/质量，不能只看新增奖励。', '', 'D1 本次以 global_wallclock_expired 结束，属于耗尽总运行预算，不能当完整年度样本。日志从 initialize 到 finish 的已接收输入仍完整；裁判发出78条请求而智能体实际收到77条，另有1条请求和1条公告在结果包中发出但未送达；需按预算耗尽末端处理，不能称为压缩日志漏项。', '', '此前本地 v4 时间对齐套件恢复的是旧 A–D，未包含新的自然语言消息、地形及事件。它仍是旧场景基准，不能称为新 A1–D1 的复刻。此次只下载和分析，没有替换默认测试集或再次提交。', '']
    for c in 'abcd':
        new=cards['v4-'+c+'1-v5'];lines += [f'## {c.upper()}1 事件概要','', '初始地形方向：'+', '.join(new['initial_terrain_directions'])+'。', '', '公告连续段：'+json.dumps(new['event_episode_counts'],ensure_ascii=False)+'。连续段可能合并多个事件，不等于隐藏事件个数。', '', '日志与结果包核对：'+json.dumps(new['export_vs_delivered'],ensure_ascii=False)+'。', '']
    (base/'run-comparison.zh.md').write_text('\n'.join(lines))
    print(json.dumps(comparison,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
