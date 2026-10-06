#!/usr/bin/env python3
"""Parse formal contestant capture and result artifacts; never read hidden truth."""
import argparse
import bisect
import collections
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import statistics
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('feedback', ROOT / 'scripts/analyze-formal-feedback.py')
feedback = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feedback)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    base = args.directory
    output = base / 'parsed'
    output.mkdir(exist_ok=True)
    summary = {}
    report_lines = ['# 正式比赛采集解析', '', '来源：单次正式评测 ed5ec77b-ac2d-450d-90df-95ff22477b75 的参赛者采集记录和结果包；没有读取官方隐藏数据。所有时间为比赛模拟 UTC 时间。', '', '连续公告段不是隐藏事件个数：同类同方向事件可能重叠合并，白天没有公告也不代表事件结束。质量是智能体选定观测方向上的反馈，不能代表全天空。', '']
    with zipfile.ZipFile(base.with_suffix('.zip')) as archive:
        for folder in sorted(base.glob('*-*')):
            if not (folder / 'captured.jsonl').exists():
                continue
            card = folder.name[-1]
            records = [json.loads(line) for line in (folder / 'captured.jsonl').read_text().splitlines()]
            inputs = [record['message'] for record in records]
            received = [msg for item in inputs if item.get('message_type') == 'decision_request' for msg in item['payload'].get('new_messages', [])]
            exported = [json.loads(line) for line in archive.read(folder.name + '/messages.jsonl').decode().splitlines()]
            # Multisets expose any missed public records or duplicates in capture.
            canonical = lambda msg: json.dumps(msg, sort_keys=True, separators=(',', ':'))
            captured_counts = collections.Counter(map(canonical, received))
            expected_counts = collections.Counter(map(canonical, exported))
            missing = expected_counts - captured_counts
            extra = captured_counts - expected_counts
            score = json.loads(archive.read(folder.name + '/score_report.json'))
            for filename, key in [('messages.jsonl', 'messages'), ('observations.csv', 'observations'), ('decisions.csv', 'decisions')]:
                assert hashlib.sha256(archive.read(folder.name + '/' + filename)).hexdigest() == score['sha256'][key]
            observations = list(csv.DictReader(io.StringIO(archive.read(folder.name + '/observations.csv').decode())))
            decisions = list(csv.DictReader(io.StringIO(archive.read(folder.name + '/decisions.csv').decode())))
            by_index = {int(row['observe_index']): row for row in decisions if row.get('action') == 'observe'}
            bulletins = [m for m in received if m['record_type'] == 'bulletin']
            episodes = feedback.notice_episodes(bulletins)
            ordered = sorted([{'event_kind': kind, **episode} for kind, items in episodes.items() for episode in items], key=lambda row: row['first_public_slot_utc'])
            requests = []
            for request in score['observation_requests']:
                window_rows = [row for row in observations if row['target_id'] in request['target_ids'] and int(row['observe_index']) in by_index and request['issued_at_utc'] <= by_index[int(row['observe_index'])]['start_utc'] < request['deadline_utc'] and row['valid'] == 'true']
                targets = []
                for tid in request['target_ids']:
                    rows = [row for row in window_rows if row['target_id'] == tid]
                    targets.append({'target_id': tid, 'valid_hits': len(rows), 'max_factor': max((float(row['factor']) for row in rows), default=None), 'completed': tid in request['completed_target_ids']})
                notices = collections.Counter(n['event_kind'] for b in bulletins if request['issued_at_utc'] <= b['issued_at_utc'] < request['deadline_utc'] for n in b['notices'])
                requests.append({**request, 'target_evidence': targets, 'window_notice_slots': dict(notices), 'timing_note': 'Hit evidence uses exposure start time; authoritative request completion is from score_report.'})
            repairs = [m for m in received if m['record_type'] == 'report_result']
            resync = [m for m in received if m['record_type'] == 'state_resync']
            conditional = []
            bulletin_times = [b['issued_at_utc'] for b in bulletins]
            for kind in sorted(episodes):
                # Slotwise association, not causal attribution. Exposure may cross a slot boundary.
                slots = {b['issued_at_utc'] for b in bulletins if any(n['event_kind'] == kind for n in b['notices'])}
                selected = []
                for row in observations:
                    decision = by_index.get(int(row['observe_index']))
                    if decision is None or row['valid'] != 'true':
                        continue
                    idx = bisect.bisect_right(bulletin_times, decision['start_utc']) - 1
                    if idx >= 0 and bulletin_times[idx] in slots:
                        selected.append(float(row['quality']))
                conditional.append({'kind': kind, 'sample_count': len(selected), 'median_quality': statistics.median(selected) if selected else None, 'zero_quality_fraction': sum(v == 0 for v in selected) / len(selected) if selected else None})
            result = {'card': card, 'score': score['total'], 'components': score['components'], 'termination': score['termination'], 'capture_records': len(records), 'message_counts': dict(collections.Counter(m['record_type'] for m in received)), 'export_reconciliation': {'missing': sum(missing.values()), 'extra': sum(extra.values()), 'missing_records': [json.loads(k) for k in missing]}, 'public_event_episode_counts': {kind: len(items) for kind, items in episodes.items()}, 'public_event_timeline': ordered, 'requests': requests, 'reports': repairs, 'state_resync': resync, 'invalidations': score['invalidations'], 'conditional_quality_association': conditional}
            summary[card] = result
            dest = output / card
            dest.mkdir(exist_ok=True)
            (dest / 'analysis.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
            (dest / 'public-messages.jsonl').write_text(''.join(json.dumps(m, ensure_ascii=False) + '\n' for m in received))
            (dest / 'initialize.json').write_text(json.dumps(records[0], ensure_ascii=False, indent=2) + '\n')
            with (dest / 'event-timeline.csv').open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=['event_kind', 'direction', 'first_public_slot_utc', 'last_public_slot_utc', 'published_slots'])
                writer.writeheader()
                writer.writerows(ordered)
            report_lines += [f'## 卡 {card.upper()}', '', f"得分 {score['total']:.6f}；接收记录 {len(records)} 条；与结果包消息核对：缺 {sum(missing.values())} 条，多 {sum(extra.values())} 条。", '', '公告连续段：' + '、'.join(f'{k} {len(v)} 段' for k, v in episodes.items()) + '。', '', f"请求完成 {sum(r['completed'] for r in requests)}/{len(requests)}；正确故障报告 {sum(bool(r['correct']) for r in repairs)} 次（故障数下限）。", '']
            for repair in repairs:
                report_lines.append(f"- 故障报告：{repair.get('issued_at_utc')}，正确={repair['correct']}，分数变化={repair['score_delta']}。这是报告时间，不是故障开始时间。")
            for request in requests:
                if request['completed']:
                    continue
                report_lines += ['', f"请求 {request['request_id']} 未完成：{request['completed_count']}/{request['minimum_completed']}，窗口 {request['issued_at_utc']} 至 {request['deadline_utc']}。"]
                for target in request['target_evidence']:
                    if not target['completed']:
                        report_lines.append(f"- {target['target_id']}：有效命中 {target['valid_hits']} 次，最高 factor={target['max_factor']}。")
                report_lines.append('窗口内公告次数：' + json.dumps(request['window_notice_slots'], ensure_ascii=False) + '；时间重合不能单独证明失败原因。')
            for item in resync:
                report_lines += ['', '数据丢失通知：`' + json.dumps({k: item[k] for k in ('issued_at_utc', 'trigger_event_id', 'invalidated_window')}, ensure_ascii=False) + '`；完整恢复状态保存在 analysis.json。']
            report_lines += ['', '公告期间选定目标的观测质量（仅为时间关联，不是事件强度）：', '', '| 公告种类 | 有效目标样本数 | 质量中位数 | 零质量比例 |', '| --- | ---: | ---: | ---: |']
            for association in conditional:
                median = association['median_quality']
                zero = association['zero_quality_fraction']
                report_lines.append(f"| {association['kind']} | {association['sample_count']} | {median if median is not None else '无样本'} | {format(zero, '.2%') if zero is not None else '无样本'} |")
            report_lines += ['', f'详细时间线与字段见 `{card}/event-timeline.csv`、`{card}/analysis.json`。', '']
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    report_lines += ['## 能还原到什么程度', '', '可以还原实际收到的公告、预测、请求、故障报告结果和数据丢失窗口，以及它们的模拟时间；观测方向、分配目标、曝光时间与得分可从同次结果包关联。', '', '不能直接还原每个隐藏事件的精确开始/结束、实际扇区、强度、故障起点、指向偏移。生成器在公告中只发布种类和粗方向，故障不进公告；同类同方向事件会合并。观测中的 factor/quality 是选定目标上的结果，多个影响会叠加，不能把一次下降直接归给一个事件。', '', '本次没有修改本地任务卡，也没有把收到的公告冒充生成器隐藏事件。后续如构造对齐场景，应把公告时间线和推测的强度分开保存并明确来源。', '']
    (output / 'report.zh.md').write_text('\n'.join(report_lines))
    print(json.dumps({c: {'score': r['score'], 'episodes': r['public_event_episode_counts'], 'messages': r['message_counts'], 'reconciliation': r['export_reconciliation'], 'requests_completed': sum(x['completed'] for x in r['requests']), 'repairs': r['reports']} for c, r in summary.items()}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
