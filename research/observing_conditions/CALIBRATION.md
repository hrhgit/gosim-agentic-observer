# 按公开预测距离和历史调整范围与概率

本轮实现上一轮确认的第1、2项：范围调整和概率调整。**没有重新训练网络，也没有改变有效比例的网络结构。**原约3万条GRU为主要候选，约6万条GRU和约3万条树模型为参照。网络输出仍是逐位置的质量、有效比例、受阻和档位概率，没有策略、推荐、排序或候选截断。

## 比较方法

| 名称 | 做法 | 要区分的变化 |
|---|---|---|
| old_global | 上轮4个校准场景的原有调整 | 已有方法 |
| global | 12个场景，按完整场景和决策组等权的全局调整 | 增加校准覆盖的影响 |
| global_sides | 相同场景，分别调整范围两侧 | 两侧误差是否不同 |
| horizon_sides | 再区分15/30/60分钟和下一夜 | 不同预测距离的不确定性 |
| history_horizon_sides | 再区分无历史、1–7次、8次历史 | 实测信息是否充分 |

上下边界分别使用校准答案与原边界的经验5%和95%差值，可以扩宽，也可以缩窄；保留原中间值和均值。质量仅限制为非负，不限制为≤1；有效比例保留[0,1]范围。调整结果始终保持上下边界与中间值顺序。

概率仍用温度调整：受阻一个参数、三类档位共用一个参数。一个组至少有8个独立场景才单独调整范围；概率还要求各结果至少出现于3个独立场景，否则沿用较大组的概率调整并保留支持不足标记。不把多个相邻样本当作独立天气。

**运行时分组仅使用预测距离和已完成的观测次数。**不使用场景编号、天气变化标签、隐藏天气水平或事件强度。历史不足组额外使用每场景前8次真实连续曝光的候选答案；这些额外诊断不混入常规采样汇总。

## 数据与选择

- 12个校准场景：原4个校准场景，加数据量研究中8个新场景。后8个没有参与本轮比较的三个冻结模型的训练。
- 12个开发场景：原4个验证场景，加上一轮8个最终核查场景。后8个现在明确转作开发数据，不能继续叫本轮独立测试。
- 8个新最终场景：4个正常、4个天气变化更强的完整连续轨迹。模型和调整参数固定后才生成；正式A–D公开文件逐字节保持原样。全部天气、事件和请求明确标记为自行生成，不代表正式隐藏数据。
- 本轮只在主要GRU上选择调整方式，其他两个模型报告参照结果。先检查总体范围与概率，以及独立场景足够的下一夜、无历史、部分历史；合格后比较范围评分和概率误差。
- 如果没有方案通过，选定一个研究候选并明确标记未通过，不依据新最终测试继续修改参数。
- 质量覆盖率目标85%–95%，概率分组偏差目标≤5%；同时检查范围宽度和同时惩罚过宽/漏值的评分。有效比例常等于0或1，额外分项的覆盖超过95%须说明保守性，不能通过随机丢掉正确答案来凑覆盖比例。
- 指标差异按完整场景重复抽样500次。仅12个校准场景及8个最终场景，不能宣称每一条预测或改变分布后都有90%的覆盖保证。

## 入口与证据

实现：`conditional_calibration.py`、`calibration_experiment.py`、`calibration_audit.py`、`conditional_predict.py`。

输出目录：`artifacts/observing_conditions-calibration/`。主要文件包括协议、冻结参数、报告、校准支持数量、概率分组表、官方答案核对、公开入口核对及`selected/`独立模型包。预测入口沿用原公开输入，保留全部候选；逐行附上调整组、支持场景数和概率支持不足标记，不插入文字报告。

```sh
research/observing_conditions/.venv/bin/python -m research.observing_conditions.tree_runtime research.observing_conditions.calibration_experiment freeze
research/observing_conditions/.venv/bin/python -m research.observing_conditions.calibration_audit contract
research/observing_conditions/.venv/bin/python -m research.observing_conditions.tree_runtime research.observing_conditions.calibration_experiment test --workers 4
research/observing_conditions/.venv/bin/python -m research.observing_conditions.calibration_audit official
research/observing_conditions/.venv/bin/python -m research.observing_conditions.tree_runtime research.observing_conditions.calibration_experiment evaluate
research/observing_conditions/.venv/bin/python -m research.observing_conditions.tree_runtime research.observing_conditions.calibration_experiment export
research/observing_conditions/.venv/bin/python -m research.observing_conditions.calibration_audit runtime
research/observing_conditions/.venv/bin/python -m research.observing_conditions.conditional_predict --input artifacts/observing_conditions-calibration/example_request.json --output /tmp/conditional-prediction.json
```

生成的大文件和本地结果默认不提交。原正式智能体、旧模型、旧预测入口保持原样；新包是独立研究入口，其最终通过状态随输出提供。
