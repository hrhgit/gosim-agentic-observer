"""Self-contained research report with actual coverage and failed acceptance goals."""
from __future__ import annotations
import html
import json
from pathlib import Path


def report_html(lines):
    """Render the fixed report structure using only the standard library."""
    blocks=[];index=0
    while index<len(lines):
        line=lines[index]
        if line.startswith('#'):
            level=min(6,len(line)-len(line.lstrip('#')))
            blocks.append(f'<h{level}>{html.escape(line.lstrip("# "))}</h{level}>')
        elif line.startswith('|'):
            rows=[];first=True
            while index<len(lines) and lines[index].startswith('|'):
                row=lines[index]
                if not row.startswith('|---'):
                    tag='th' if first else 'td';first=False
                    cells=''.join(f'<{tag}>{html.escape(value.strip())}</{tag}>'
                                  for value in row.strip('|').split('|'))
                    rows.append('<tr>'+cells+'</tr>')
                index+=1
            blocks.append('<div class="table"><table>'+''.join(rows)+'</table></div>')
            continue
        elif line.startswith('```'):
            code=[];index+=1
            while index<len(lines) and not lines[index].startswith('```'):
                code.append(lines[index]);index+=1
            blocks.append('<pre>'+html.escape('\n'.join(code))+'</pre>')
        elif line:
            blocks.append('<p>'+html.escape(line)+'</p>')
        index+=1
    style='body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px;color:#172335;line-height:1.7}table{border-collapse:collapse;min-width:70%}.table{overflow:auto;margin:18px 0}th,td{border:1px solid #ddd;padding:8px 12px;text-align:left;white-space:nowrap}th{background:#edf3f7}pre{white-space:pre-wrap;background:#f3f5f7;padding:18px}h2{margin-top:36px}'
    return '<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>观测条件模型比较</title><style>'+style+'</style><body>'+''.join(blocks)+'</body></html>'


def write_report(output,report):
    output=Path(output)
    manifest=json.loads((output/'manifest.json').read_text())
    generation=json.loads((output/'generation_complete.json').read_text())
    audit=json.loads((output/'audit.json').read_text())
    selected=report['selection']
    lines=['# 观测条件模型比较报告','',
        f'选定架构：**{selected.upper()}**。全部验收目标：**{"通过" if report["passed"] else "未通过"}**。','',
        '结果来自正式 A–D 的公开输入与自行生成天气，不能作为正式比赛成绩。仪器效率固定为 1；模型不判断隐藏仪器故障。','',
        f'数据：{generation["scenarios"]} 个独立场景，{generation["samples"]:,} 条候选曝光；训练/验证/校准/主测试各 224/32/32/32 个场景，另有 32 个天气变化测试和 32 个新站点测试。',
        f'答案核对：{audit["checked"]} 条，最大质量差异 {audit["maximum_quality_error"]:.9g}。','',
        '## 主测试比较','',
        '| 模型 | 质量 MAE | 总体相对误差 | 有效比例误差 | 受阻 Brier | 档位 F1 | 90% 区间覆盖 | 位置选择损失 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name in ('constant','lookup','mlp','gru','tcn'):
        m=report['tests']['test'][name]
        lines.append(f'| {name} | {m["quality_mae"]:.4f} | {m["quality_wape"]:.1%} | {m["usable_mae"]:.4f} | {m["blocked_brier"]:.4f} | {m["band_macro_f1"]:.3f} | {m["interval_coverage"]:.1%} | {m["ranking_loss"]:.1%} |')
    lines+=['','## 验收结果','', '| 检查 | 结果 |','|---|---|']
    labels={'quality_relative':'质量误差比查表降低至少 15%',
        'quality_absolute':'总体相对误差不超过 20%','usable':'有效比例误差不超过 0.10',
        'blocked_probability':'受阻概率误差比查表降低至少 10%','bands':'三档平均 F1 至少 0.80',
        'probability_reliability':'概率分组偏差不超过 0.05','interval_coverage':'区间覆盖在 85%–95%',
        'interval_score':'区间评分优于查表','ranking':'位置选择损失比查表降低至少 10%',
        'latency':'1,024 候选纯推理 p95 不超过 100 ms'}
    for key,value in report['acceptance'].items():
        lines.append(f'| {labels[key]} | {"通过" if value else "未通过"} |')
    lines+=['','## 按完整场景计算的差异范围','',
        '以下为选定模型减去统计查表的结果。误差和损失为负表示较好，F1 为正表示较好。每次抽取完整场景后重新计算整体指标，共重复 2,000 次。','',
        '| 指标 | 差异 | 95% 范围 |','|---|---:|---|']
    metric_labels={'quality_mae':'质量平均绝对误差','quality_wape':'总体相对误差',
        'usable_mae':'有效比例误差','blocked_brier':'受阻概率误差','band_macro_f1':'档位平均 F1',
        'blocked_ece':'受阻概率分组偏差','band_ece':'档位概率分组偏差',
        'interval_coverage':'区间覆盖','interval_score':'区间评分','ranking_loss':'位置选择损失'}
    for key,label in metric_labels.items():
        interval=report['tests']['test']['scene_intervals'][key]
        low,high=interval['ci95']
        lines.append(f'| {label} | {interval["metric_difference"]:+.4f} | {low:+.4f} 至 {high:+.4f} |')
    lines+=['','## 选定架构的三次初始化','',
        '| 种子 | 质量 MAE | 受阻 Brier | 档位 F1 | 区间覆盖 |','|---|---:|---:|---:|---:|']
    for seed in (101,202,303):
        m=report['tests']['test'][f'{selected}-{seed}']
        lines.append(f'| {seed} | {m["quality_mae"]:.4f} | {m["blocked_brier"]:.4f} | {m["band_macro_f1"]:.3f} | {m["interval_coverage"]:.1%} |')
    lines+=['','## 训练记录','',
        '| 架构 | 选定学习率 | 设备 | 初始化种子 | 每次完整轮数 | 验证可靠性 |',
        '|---|---:|---|---|---:|---|']
    for name in ('mlp','gru','tcn'):
        comparison=report['validation'][name]
        lines.append(f'| {name} | {comparison["learning_rate"]} | {report["device_benchmarks"][name]["selected"]} | 101 / 202 / 303 | 60 | {"通过" if comparison["validation_reliable"] else "未通过"} |')
    lines+=['','每种架构另以种子 404、固定训练子集比较两种学习率，各 20 轮。每次 60 轮均完成；交付权重来自综合验证误差最小的轮次。',
        '温度缩放与区间调整使用单独的 32 个校准场景。最终主测试有 32 个独立场景，共 131,072 条记录。',
        '所有场景种子与集合在 manifest.json；各场景天气参数在 scenarios/<场景>/weather_config.json。主数据天气幅度上下浮动 10%，事件数量及持续时间乘以 0.75–1.25；外部天气测试分别扩大为 25% 和 0.5–1.5。夜间连续性为 0.70–0.90，时段连续性为 0.80–0.95。']
    lines+=['','## 变化后的输入与删减实验','',
            '| 实验 | 质量 MAE | 档位 F1 | 区间覆盖 |','|---|---:|---:|---:|']
    for key in ('weather_test','site_test'):
        m=report['tests'][key][selected]
        lines.append(f'| {key} | {m["quality_mae"]:.4f} | {m["band_macro_f1"]:.3f} | {m["interval_coverage"]:.1%} |')
    for key,m in report['leave_card_out'].items():
        lines.append(f'| 留出卡 {key.upper()} | {m["quality_mae"]:.4f} | {m["band_macro_f1"]:.3f} | {m["interval_coverage"]:.1%} |')
    lines+=['','## 删除输入后的比较','',
        '以下均使用相同架构、学习率和初始化种子 101，各训练 60 轮。完整输入使用单模型结果，避免把三个模型平均带来的变化混入删减比较。删除历史仍保留当前公告与已经公开的固定地形方向。','',
        '| 输入 | 质量 MAE | 有效比例误差 | 受阻 Brier | 档位 F1 |','|---|---:|---:|---:|---:|']
    variants={'完整输入':report['tests']['test'][f'{selected}-101'],
              '删除公告历史':report['ablations']['no_history'],'删除预报':report['ablations']['no_forecast']}
    for name,m in variants.items():
        lines.append(f'| {name} | {m["quality_mae"]:.4f} | {m["usable_mae"]:.4f} | {m["blocked_brier"]:.4f} | {m["band_macro_f1"]:.3f} |')
    lines+=['','## 运行与追溯','',f'选定架构的三次初始化输出平均，CPU 单线程纯推理 p95：{report["cpu_ensemble"]["p95_ms"]:.2f} ms / 1,024 个候选。此数字不包含特征处理和概率校准。',
        '测速使用一个共享公告历史、开始时间和曝光时长；32 个不同位置特征重复到 1,024 个候选。计时包括网络、输出概率转换和三个模型的平均。',
        '每个预测请求另报告完整输入处理与预测耗时。设备选择依据本机 CPU/MPS 的实测训练步骤耗时，具体数值在 device_benchmarks.json。']
    request_path=output/'request_benchmark.json'
    if request_path.exists():
        timing=json.loads(request_path.read_text())
        t=timing['timings_ms']
        lines+=['',f'完整公开请求另测 {timing["repetitions"]} 次，CPU 单线程、1,024 个候选；32 个不同位置重复，共享当前公告及曝光设置。',
            f'输入处理 p95：{t["preprocessing_seconds"]["p95"]:.2f} ms；预测与校准 p95：{t["prediction_seconds"]["p95"]:.2f} ms；完整请求 p95：{t["total_seconds"]["p95"]:.2f} ms。各部分分位数不直接相加。',
            '完整请求计时不含 JSON 读取和模型初始化，逐候选计算位置、月亮及大气条件；详细记录在 request_benchmark.json。']
    lines+=['',
        '完整分项指标、单模型差异、按场景重复抽样的误差范围：report.json。输入与答案：scenarios/*/samples.npz；来源、种子、划分和源文件哈希：manifest.json。',
        '最佳权重、预处理与校准参数：models/selected/。各训练任务保存 history.jsonl、完整轮数和可恢复的 latest.pt。','',
        '留卡实验固定采用主验证集选定的架构与学习率，仅权重训练、归一化和校准排除该卡；它不是重新进行架构选择的嵌套验证。',
        '质量区间采用独立场景校准；相关记录并非独立样本，分布变化后的覆盖率没有保证。模型不接入智能体，也没有线上提交。','',
        '交付元数据与预测响应中的 final_test_passed 明确记录最终验收是否通过；validation_reliable 只描述验证集阶段，不代表最终验收。','',
        f'官方代码版本：{manifest["git_revision"]}。正式公开输入的哈希与生成器哈希保存在 manifest.json。',
        '研究代码与依赖锁文件的哈希保存在 implementation_sources.json；依赖版本在 research/observing_conditions/uv.lock。','',
        '## 重跑命令','',
        '从项目根目录运行，改用新目录可以保留本次结果：','',
        '```sh',
        'uv sync --project research/observing_conditions --python 3.12 --locked',
        'uv run --project research/observing_conditions python -m research.observing_conditions run --output artifacts/observing_conditions-rerun --workers 4',
        'uv run --project research/observing_conditions python -m research.observing_conditions predict --input artifacts/observing_conditions/example_request.json --result artifacts/observing_conditions/example_prediction.json',
        '```']
    markdown='\n'.join(lines)+'\n'
    (output/'report.md').write_text(markdown)
    (output/'report.html').write_text(report_html(lines))
