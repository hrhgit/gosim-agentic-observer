# 是否需要提高观测条件模型的数据量

本轮只检查数据量与采样覆盖，沿用 [连续反馈模型](ADAPTATION.md) 的公式、冻结基础网络、输入、答案、GRU32 和树模型结构。所有数据来自正式 A–D 公开输入与明确标注的自行生成天气、事件及请求。结果不代表正式隐藏卡表现。

## 比较方案

| 方案 | 训练场景 | 每场景决策时刻 | 作用 |
|---|---:|---:|---|
| 32 | 16 | 32 | 从原64组按完整决策组抽一半 |
| 64 | 16 | 64 | 原30574条训练记录与原权重，直接复用 |
| 128 | 16 | 128 | 保留原64组，再从完整连续轨迹抽64组 |
| balanced64 | 16 | 64 | 56个原决策时刻加前8次曝光，补足历史刚开始积累的阶段 |
| diverse64 | 24 | 64 | 原16场景加8个新场景，其中4个正常天气、4个更强天气变化 |

每个决策时刻仍是8个不同位置、15/30/60分钟及下一夜、一次60–3600秒曝光。32/64/128是**训练采样时刻数**，不是候选保留数量。新模型没有选择、排序、截断或排除位置。

原训练数据中仅64条记录有1–7次历史，来自2个场景；原验证集没有这类记录。新诊断额外查询每个场景前8次曝光。诊断与常规采样分别报告，不能混成自然使用比例；二者可能有重复决策，也不增加独立场景数。

## 固定设置与防止混用

- 每个新方案只训练一次、一个初始化种子。GRU固定10轮、批次512、AdamW学习率0.001；树模型各输出固定拟合一次。不做参数搜索或多种子平均。
- 原64组GRU及树模型直接复用，表格中的新训练时间为0；不表示原模型没有训练成本。
- 本轮不启用运行时参数更新。基线当前估计及最新实测历史继续作为输入更新。
- 训练、验证、校准按完整天气场景分开；增加的训练场景与最终核查场景使用不同独立种子。
- 继续使用原4个验证场景和4个校准场景作开发比较。原最终测试不读取、不参与选择；冻结全部方案、权重和校准后才生成8个新最终测试场景。
- 选型沿用验证集概率/范围可靠性检查，再比较质量分位数、有效比例分位数、受阻概率、档位概率四项综合误差。没有依据新最终测试重新训练或校准。
- 质量范围评分同时惩罚范围过宽和漏掉真实值。范围覆盖目标85%–95%，两种概率分组偏差目标≤5%；单点误差辅助说明。
- 指标差异按完整场景重复抽样500次，提供95%范围。8个最终场景仍不足以证明各类天气都可靠，也没有测量初始化种子差异。
- 增加行数在固定轮数下也增加训练步数，因此不是相同计算量比较；增加场景方案同时增加天气变化类型，不能把改善全部归于场景数。

## 重跑

在项目根目录运行，使用已有研究环境。生成过程覆盖完整连续观测周期，不做每夜固定8次安排。

```sh
research/observing_conditions/.venv/bin/python -m research.observing_conditions.tree_runtime research.observing_conditions.adaptation_scaling development --workers 4
research/observing_conditions/.venv/bin/python -m research.observing_conditions.adaptation_scaling_audit development
research/observing_conditions/.venv/bin/python -m research.observing_conditions.tree_runtime research.observing_conditions.adaptation_scaling freeze
research/observing_conditions/.venv/bin/python -m research.observing_conditions.tree_runtime research.observing_conditions.adaptation_scaling test --workers 4
research/observing_conditions/.venv/bin/python -m research.observing_conditions.adaptation_scaling_audit test
research/observing_conditions/.venv/bin/python -m research.observing_conditions.tree_runtime research.observing_conditions.adaptation_scaling evaluate
```

生成记录、权重、范围调整参数、核对证据和比较表保存在 `artifacts/observing_conditions-data-scaling/`；默认不提交大文件。原实验目录和预测入口保持原样，这轮候选权重还没有替换到智能体。完整输出见 `report.md`、`report.json`，实验开始时固定的规则见 `protocol.json`，选型与训练成本见 `frozen.json`。
