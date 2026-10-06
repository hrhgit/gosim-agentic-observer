# 正式卡线上时间对齐版 v4

本套数据重建已经收到的正式公告、预测和请求，时间与内容原样保留；隐藏天气与事件参数由本地假设补齐，**不是官方隐藏数据，不保证本地分数等于线上**。原公开输入和旧评测集均未修改。

每卡 `generator/event-evidence.json` 区分观察到的时间段与推测的范围/强度/故障起点，`self_generated_metadata.json` 列出全部假设和来源。事件衍生公告已独立验证与收到的正式公告逐槽一致；没有收到公告的尾部槽位不冒充已知，故障报告真假与已记录结果一致。

地震幅度由公告持续夜数在假定衰减规则下推算；若参考衰减太短，增加到能解释公告持续夜数的值，不能当作测得震级；方向扇区宽度假定60度、最高高度60度，严重程度0.75。故障只恢复成功修复所证明的数量下限，开始时间假定为修复夜开端，效率0.55。D 指向偏差暂取0，数据丢失按已收到通知时间和比例重建，换行动后失效窗口会改变。后台天气使用新固定种子，不拟合完整天况；不额外加入随机关闭。

```sh
python3.12 examples/_local/runner/run_local.py --card evaluation_assets/formal-v4-observed-timeline-v4/cards/a --agent "python3.12 agent.py" --agent-cwd agents/claude-baseline-v1/agent --out artifacts/formal-feedback-2026-10-05/local-timeline-v4/a --quiet
```

本地工具默认使用本套；旧v3可通过 `--eval-set formal-v4-feedback-self-generated-v3/nominal` 显式选择。构建命令：`python3 scripts/reconstruct-formal-timeline.py --out evaluation_assets/formal-v4-observed-timeline-v4-copy`。

## 与线上观测抽样核对

每卡选取256条均匀分布的有效目标观测，固定线上曝光时间、命中目标及节目倍率，使用本地推测天气重算观测质量。没有用这些样本调整参数，也没有运行完整智能体或重算最终总分。

| 卡 | 非零观测质量相对误差中位数 | 误差不超过10%的样本比例 |
|---|---:|---:|
| A | 24.6% | 21.6% |
| B | 17.0% | 30.0% |
| C | 23.8% | 19.7% |
| D | 20.7% | 24.4% |

这些差距表明公告时间虽已对齐，隐藏天气数值仍没有恢复。新数据适合测试事件时间线与响应过程；不能用于声称本地得分与线上一致。详细样本在每卡 generator/quality-comparison-sample.csv。
