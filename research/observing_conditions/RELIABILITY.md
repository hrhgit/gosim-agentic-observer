# 观测条件模型：当晚历史与有效比例端点

本轮提炼客观信息。预测对象仍是一个位置的一次曝光；不选择、排序、删减或排除候选。实验报告仅供开发核查，预测入口不把报告正文发给智能体。

## 改动

- 保留完整的最近8条已完成曝光；另外计算当晚已经完成几次。已有上一夜记录和当晚有新测量分别表达。
- 从完整连续观测轨迹抽取多个夜晚开头、当晚早期的真实决策状态。额外采样改变评测表，不改变观测过程。
- 冻结原GRU、公式和基础模型。比较小规模的概率调整：温度、温度加类别偏移、向总体缩回的类别偏移。
- 一个1109参数的小头输出 `P(U=0)`、`P(0<U<1)`、`P(U=1)`，中间部分使用两个参数的分布；有效比例均值、分位数和严重受阻概率从同一个分布计算。一次初始化，12轮，训练约2秒。
- 首次观测时没有BRIGHT/BACKUP等结果的充分独立证据，输出明确标为证据不足。高概率不能据此当作确定结论。

## 分开理解两类改进

当晚信息分组改进质量范围。概率类别偏移仅在首次观测保留；在其他条件没有稳定收益。额外8个旧场景使校准增至20场景，也没有解决首次观测的少见档位偏差，最终沿用12场景方案。

端点小头丰富有效比例分布。它的范围覆盖会因0/1占比很大而高于95%，不称其“精确90%校准”。单点误差、真实0/1难例和部分观测的结果也报告。原85%–95%验收规则没有为它放宽，因此整体仍须保留未通过标记。

## 数据与隔离

全部天气、事件和请求明确为自行生成；正式A–D公开输入不改，练习卡不使用。

- 小头训练：原来的16个完整场景，约5万条记录；场景完整隔离。主网络不重训。
- 校准：12个独立场景；额外8个旧场景只用于已注明的对照尝试。
- 开发选择：20个独立场景。原最终测试显式转为开发，不再称本轮独立测试。
- 最终核查：固定方法之后生成16个新场景，每卡2个普通、2个天气变化更大的场景。
- 同一决策的多个位置不能当作独立场景；差异范围按整个场景重复抽样。

中间数据、模型和最终结果位于 `artifacts/observing_conditions-reliability/`；大文件默认不提交。

## 复跑

项目根目录执行（macOS树模型与PyTorch需要共享OpenMP）：

```sh
research/observing_conditions/.venv/bin/python -m research.observing_conditions.reliability_data --workers 4
research/observing_conditions/.venv/bin/python -m research.observing_conditions.tree_runtime research.observing_conditions.reliability_experiment develop
research/observing_conditions/.venv/bin/python -m research.observing_conditions.tree_runtime research.observing_conditions.reliability_experiment endpoint
research/observing_conditions/.venv/bin/python -m research.observing_conditions.reliability_data --split calibration_extra --workers 4
research/observing_conditions/.venv/bin/python -m research.observing_conditions.tree_runtime research.observing_conditions.reliability_experiment augment
research/observing_conditions/.venv/bin/python -m research.observing_conditions.tree_runtime research.observing_conditions.reliability_experiment freeze
research/observing_conditions/.venv/bin/python -m research.observing_conditions.tree_runtime research.observing_conditions.reliability_experiment test --workers 4
research/observing_conditions/.venv/bin/python -m research.observing_conditions.tree_runtime research.observing_conditions.reliability_experiment evaluate
research/observing_conditions/.venv/bin/python -m research.observing_conditions.tree_runtime research.observing_conditions.reliability_experiment export
research/observing_conditions/.venv/bin/python -m research.observing_conditions.reliability_audit all
research/observing_conditions/.venv/bin/python -m research.observing_conditions.reliability_completion
```

完整轨迹和已训练小头存在时会复用；不重复初始化。阶段记录、种子、来源哈希、方法冻结、校准参数和失败分项分别保存。最终核查不可再用于调参并继续称为独立测试。

## 批量预测

```sh
research/observing_conditions/.venv/bin/python -m research.observing_conditions.reliability_predict \
  --input artifacts/observing_conditions-reliability/example_request.json \
  --output artifacts/observing_conditions-reliability/cli_prediction.json
```

输入契约沿用ADAPTATION.md：公开初始化信息、当时的公告/预报、当前估计、已完成曝光历史、候选数组。支持未来15/30/60分钟和下一夜开始，申报60–3600秒，夜末使用实际时长。

输出保留全部候选，逐候选给质量均值/范围、相对同一未来几何条件的当前估计变化、有效比例均值/范围、严重受阻概率、三种档位概率。新增有效比例端点三种概率、当晚历史数和分组证据。明确研究版本、适用范围、是否通过完整检查和不能保证每一候选的说明。

当晚历史数统计的是已完成曝光反馈；并不保证每条都含可还原的实测通量。饱和、未命中和模糊档位的标记仍保留，当前天气估计是否已有实测依据另行给出。

数据和权重只在本机科研流程验证。压缩权重包需要本项目的代码及依赖；未接入正式智能体，未核验本轮Linux容器或正式隐藏比赛效果。

## 停止依据

这轮已经比较了历史分组、两种类别比例调整、增加独立校准场景及一次端点头。只保留有开发依据的改进，最终测试不用于再调模型。剩余首次观测的稀缺档位与有效比例极端难例明确暴露；扩大训练量、重复初始化或再换架构没有本轮证据支持。后续若推进，应优先补独立早期天气分布证据，或验证智能体实际调用价值。
