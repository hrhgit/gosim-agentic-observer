"""Disable failed capabilities and bundle evidence; never retune from final tests."""
from __future__ import annotations

import json
from pathlib import Path

from .data import save_json
from .improvement import DEFAULT_IMPROVEMENT, write_report


def finalize(output=DEFAULT_IMPROVEMENT):
    output=Path(output)
    report=json.loads((output/'report.json').read_text())
    frozen=json.loads((output/'frozen.json').read_text())
    priority=json.loads((output/'priority/report.json').read_text())
    policy_path=output/'models/deployment/policy.json'
    policy=json.loads(policy_path.read_text())
    exclusion_checks={};recommendation_checks={}
    for split,models in report['tests'].items():
        m=models['selected'];bounds=m['scene_95_intervals']
        exclusion_checks[split]=(m['blocked_excluded']>0
            and m['blocked_false_exclusion_rate']<=.01
            and bounds['blocked_false_exclusion_rate'][1]<=.02
            and (m['near_best_loss_rate'] or 0)<=.01)
        recommendation_checks[split]=(m['recommendation_groups']>=100
            and (m['recommendation_failure_rate'] or 0)<=.01
            and bounds['recommendation_failure_rate'] is not None
            and bounds['recommendation_failure_rate'][1]<=.02)
    acceptance=dict(strict_exclusion=all(exclusion_checks.values()),
                    direct_recommendation=all(recommendation_checks.values()),
                    priority_shortlist=priority['empirical_test_passed'])
    # Preserve the evaluated thresholds in evidence; disabling a failed capability
    # is a fixed deployment gate, not a new optimized threshold.
    policy['evaluated_assistance_rule']=frozen['assistance']['rule']
    policy['assistance_rule']=frozen['assistance']['rule'] | dict(
        exclude_enabled=acceptance['strict_exclusion'],recommend_enabled=acceptance['direct_recommendation'])
    policy['priority_rule']=priority['protocol']['selected']['rule']
    policy['priority_test_passed']=acceptance['priority_shortlist']
    policy['assistance_acceptance']=acceptance
    policy['assistance_test_passed']=all(acceptance.values())
    policy['automatic_actions_enabled']=False
    policy['validation_evidence']=dict(strict=frozen['assistance']['validation'],
        priority=priority['protocol']['selected']['validation'])
    policy['test_evidence']=dict(exclusion_checks=exclusion_checks,
        recommendation_checks=recommendation_checks,priority_tests=priority['tests'])
    save_json(policy_path,policy)
    report['assistance_acceptance']=acceptance
    report['timing']['training_cumulative_seconds']=sum(json.loads((output/'models'/name/'complete.json').read_text())['elapsed_seconds']
        for name in ('compact','physics','physics_augmented','oracle_diagnostic'))
    report['timing']['timing_note']='Phase times are from the resumed invocation; training_cumulative_seconds includes all saved training epochs.'
    save_json(output/'report.json',report)
    write_report(output,report,frozen)
    benchmark=json.loads((output/'batching_benchmark.json').read_text())
    lines=['','## 实际能力和批量计算改进','',
        f'严格排除：{"通过" if acceptance["strict_exclusion"] else "未通过，交付入口关闭"}；直接代选：{"通过" if acceptance["direct_recommendation"] else "未通过，交付入口关闭"}；优先清单：{"通过经验测试" if acceptance["priority_shortlist"] else "未通过经验测试，只提供研究建议"}。',
        '严格排除在较强天气变化场景误排率显著上升，不能因为模型受阻概率很高就自动排除。失败能力只关闭，没有用测试结果重新调整阈值。',
        '优先清单使用另外32个独立场景检验；规则与结果见 priority/report.md。',
        '相同清单长度下，公式参照在三个集合均未丢失好位置，优于本轮神经网络清单；因此不能把减少87.5%比较的收益归因于网络。当前数据未证明网络优先排序有额外价值。',
        '', '| 请求 | 改进前完整请求 p95 | 改进后完整请求 p95 | 最大数值差异 |','|---|---:|---:|---:|']
    for name,m in benchmark['cases'].items():
        lines.append(f'| {name} | {m["timings_ms"]["before"]["p95"]:.2f} ms | {m["timings_ms"]["after"]["p95"]:.2f} ms | {m["max_numeric_difference"]:.3g} |')
    lines+=['','CPU单线程、同一原模型权重及校准，包含公开输入处理、网络与校准；不含JSON解码和模型初始化。每项10次，分位数是本次样本统计。',
        '', '## 五个方向的结论','',
        '1. **信息缺失诊断**：真实天气诊断模型明显更准确，说明公开消息缺失的信息确实构成限制；它不是部署模型或严格误差下限。',
        '2. **公式分工**：已实现可追溯的几何因子与网络环境部分组合；本轮单次、小训练子集比较未改善整体质量误差，保留对照，不宣称优化成功。',
        '3. **历史摘要**：291个摘要值替代32×75原历史；数值准确度未超过原单模型，但在验证的严格排除覆盖上较好，因此按新用途选定。外部天气排除失效，部署关闭该能力。',
        '4. **独立天气场景**：补充32训练场景（16正常、16较强变化）、56冻结后测试场景；优先清单另用32场景。扩大天气训练没有证明精度提升。',
        '5. **可靠性与速度**：比较全局和按预测距离/曝光长度分组的校准，只在验证集选方法；失败的建议能力关闭。批量输入计算实测加速，数值等价检查通过。',
        '', '四次新训练共80轮、各一次初始化，累计训练时间约 '+f'{report["timing"]["training_cumulative_seconds"]:.1f} 秒。训练数据为原每场景16组，加数据方案额外使用新32场景全部64组；原参照使用第一轮已有60轮全量权重，训练预算不同，本轮不能隔离得出架构或特征本身更优的结论。',
        '只有观测条件比较；未实测智能体任务完成率、耗时或正式比赛成绩。']
    with (output/'report.md').open('a') as f:f.write('\n'.join(lines)+'\n')
    print(json.dumps(acceptance))
    return acceptance


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=DEFAULT_IMPROVEMENT)
    args=parser.parse_args();finalize(args.output)
