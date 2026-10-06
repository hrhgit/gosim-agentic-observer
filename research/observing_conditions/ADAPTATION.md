# 连续观测反馈修正：小数据实验

本轮保留原公式与基础网络，只训练新增反馈修正部分。代码不修改原基础权重，也不改变原来的 24 区信息入口。新入口返回每个指定位置的信息，保留全部候选。

## 实际完成

32 个独立、自行生成的天气场景，A–D 各 8 个；正式公开文件逐字节保持一致。训练 16 场景，验证 4，校准 4，测试 8。每个场景连续运行整个正式公开日程，抽取 64 个真实决策时刻、8 个天空位置、15/30/60 分钟与下一夜开始四种距离。最低高度不满足的记录不用于质量训练。实际训练 30,574 条、验证 7,558 条、校准 7,733 条、测试 15,257 条。

训练只有一次初始化：32 单元 GRU，29,453 个参数、10 轮；LightGBM 的 10 个输出各拟合一次，没有搜索或重复训练。新增网络接收相同公开条件、基础预测、当前估计和最近 8 次曝光的反馈。所有数据集按完整天气场景划分。

数据与产物在 `artifacts/observing_conditions-adaptation/`，不提交生成数据、环境或大文件。原始完整轨迹是 `scenarios/*/exposures.jsonl.gz`；抽取决策、候选输入和答案分别保存。`protocol.json` 固定数据来源、种子与划分；`frozen.json` 固定选择、规则、权重散列与校准；`report.json` 保存全部指标、分项与场景重复抽样差异区间。

后续[数据量比较](DATA_SCALING.md)单独存放于 `artifacts/observing_conditions-data-scaling/`：比较约1.6万、3万、6万条，以及补初始历史、增加独立场景；使用新的最终核查场景，未替换这里的模型。样本翻倍的范围评分改善约2%–3%，差异尚不稳定，天气变化和下一夜仍有覆盖不足，因此暂不扩大到几十万条。

随后[按预测距离和历史调整范围与概率](CALIBRATION.md)，复用冻结权重、网络重训0次，产物在 `artifacts/observing_conditions-calibration/`。8个新场景核查中，约3万条GRU的质量范围评分比相同校准数据的全局方法改善约1.5%；下一夜档位概率偏差降到3.5%，部分历史仍为6.6%，整体未通过。新公开入口独立保留逐候选数值与证据不足标记，没有替换旧入口或正式智能体。

## 输入与职责

1. 公式计算太阳/月亮、位置移动、整次曝光最低高度、实际时长等已知量。
2. 原 GRU 用已发布公告与预报给出基础信息；其权重冻结。原模型训练距离只到 60 分钟，下一夜使用明确标为尝试性应用。
3. 修正模型加入最近 8 次曝光、真实时间间隔、当前基线天气/仪器估计，预测未来相对“当前状态保持不变”的变化。模型提供客观信息，不选择位置、不截断、不排除候选。

实际反馈是每个命中目标的得分，并非直接给出的通量或真实天气。按公开计分公式反推测量区间；满分只能给下界，同一得分能由多种档位解释时保留未知标记。输入保留这些标记、命中比例、年龄、几何因素除去后的信号区间和公开估计状态。参数更新仅使用明确匹配、未满分、非零、未发现故障/地震影响的记录；反推答案仍依赖估计的仪器效率，不声称等同真实环境质量。

修正部分共享一次离线训练；比较固定参数、每 8 次可用曝光更新一次简单偏移、每 8 次更新一次质量输出层。每次只做一个更新步骤，保存最近 64 次曝光，各曝光等权；其他输出与基础模型不更新。每场景重置，先预测，再接收已完成曝光的反馈。更新入口不接受真实质量答案。

## 测试结论

| 方法 | 质量范围评分↓ | 90% 质量范围覆盖 | 质量平均误差↓ | 受阻概率误差↓ | 档位概率误差↓ |
|---|---:|---:|---:|---:|---:|
| 保持当前估计 | 1.8441 | 89.03% | 0.1789 | 0.0707 | 0.2653 |
| 冻结原基础网络 | 1.7182 | 86.39% | 0.3443 | 0.0531 | 0.3211 |
| 残差统计查表 | 1.7000 | 90.18% | 0.1797 | 0.0532 | 0.2323 |
| 小 GRU，固定参数 | 1.4343 | 89.71% | 0.1743 | 0.0456 | 0.1827 |
| LightGBM | 1.3443 | 87.55% | 0.1648 | 0.0409 | 0.1591 |
| GRU，在线偏移 | 1.4319 | 91.31% | 0.1687 | 0.0456 | 0.1827 |
| GRU，在线质量层 | 1.4465 | 91.85% | 0.1686 | 0.0456 | 0.1827 |

质量范围评分同时惩罚过宽与遗漏实际值，越低越好。受阻误差为二分类 Brier，档位为三类 Brier。这里的覆盖率是独立场景实测，不能解读成每一行有 90% 正确概率。

**整个验收未通过。** 验证集按概率、范围可靠性筛选后选中了在线质量层 GRU；最终测试的档位概率偏差 5.12%，超过 5% 目标。下一夜的档位偏差 8.90%，有效比例范围覆盖 83.79%。LightGBM 整体误差更低，但有效比例范围覆盖 96.13%，超过预设 95% 上限；变化场景质量范围覆盖 83.36%，C 卡覆盖 82.27%。不因测试结果改选或调参后继续称其为独立检验。

在线质量层把平均误差降低 3.26%，但质量范围评分略变差；范围评分差异的场景抽样 95% 区间含零，未达到预期的 5% 增益。计算上可行，效果依据不足以支持默认在线改参数。固定参数也能持续接收新反馈。

历史不足阶段尤其缺少可靠证据：测试中 1–7 次历史仅来自一个场景、32 条记录，质量范围覆盖 3.13%；无历史有 251 条。输出提供 `limited_history`、实际可用历史数、估计仪器状态及下一夜标记，不能把这些阶段作为高置信依据。4 个校准场景较少；本轮没有扩大数据再训练。天气变化与替代采样策略在 4 个测试场景同时出现，不能分开归因。

## 运行与耗时

开发数据生成 555.9 秒；最终测试生成 225.4 秒，均为四个工作进程。GRU 训练 2.53 秒，树模型拟合 15.12 秒。完整轨迹生成是主要成本，后续复用轨迹与权重，不需要再生成同样的场景。

完整 B 卡 5,754 次公开反馈中，可更新代理答案 3,448 条，共 249 次更新。修正模型处理 1,024 候选的本机单线程 p95 约 3.3 毫秒；含输入处理的一次批量约 17.5 毫秒。完整反馈处理从重复重建索引的 172.3 秒降到 6.9 秒；134 项索引与特征相等检查通过。

Linux x86_64、2 核、2 GB、最多 128 个进程、只读根目录、256 MB `/tmp` 的容器已跑通。修正纯推理 p95 18.1 毫秒，含输入处理约 56.3 毫秒；完整 B 卡反馈处理 13.2 秒 CPU，容器内存峰值约 395 MiB。使用 M2 的 x86 转译，CPU 时间与参考任务换算不是正式服务器计时。仅证明模型的载入、数值、公开反馈更新和资源检查；尚未接入智能体或验证比赛得分。

Mac 训练依赖保存于 `models/training_complete.json` 与项目 `uv.lock`。Linux 使用 Dockerfile 固定版本，其中 PyTorch 2.8 CPU；与 Mac 2.14 权重输出最大差 2.4e-7。镜像构建不足一分钟，依赖在构建阶段安装。产物不携带整套 Python 环境。

## 可重复执行

在项目根目录使用已有独立 Python 环境。已完成训练/生成会复用，不重复初始化训练。

```sh
research/observing_conditions/.venv/bin/python -m research.observing_conditions.adaptation_prepare --stage development --workers 4
research/observing_conditions/.venv/bin/python -m research.observing_conditions.adaptation_audit
research/observing_conditions/.venv/bin/python -m research.observing_conditions.tree_runtime research.observing_conditions.adaptation_experiment freeze
research/observing_conditions/.venv/bin/python -m research.observing_conditions.adaptation_prepare --stage test --workers 4
research/observing_conditions/.venv/bin/python -m research.observing_conditions.tree_runtime research.observing_conditions.adaptation_experiment evaluate
research/observing_conditions/.venv/bin/python -m research.observing_conditions.adaptation_predict --input artifacts/observing_conditions-adaptation/example_request.json --output /tmp/conditions-prediction.json
```

Python 的 `AdaptationPredictor.predict` 接收公开初始化、时间、已发布公告与预报、基线估计状态、近期反馈摘要及候选数组。返回逐候选质量、三个分位数、有效比例范围、受阻/档位概率、相对当前保持估计的变化范围以及适用限制。变化的参考值在同一个未来几何位置计算，避免把月亮或目标移动误认为天气变化。`reset` 用于新场景；`update` 仅在反馈完成后可选调用，不含答案真值。正式目标目录在初始化后保持不变；公告内容修改也会使缓存失效。

```sh
docker build --platform linux/amd64 -f research/observing_conditions/adaptation.runtime.Dockerfile -t observing-conditions-adaptation:cpu .
docker run --rm --platform linux/amd64 --cpus 2 --memory 2g --pids-limit 128 --read-only --tmpfs /tmp:rw,size=256m -v "$PWD:/workspace:ro" -v "$PWD/artifacts/observing_conditions-adaptation:/workspace/artifacts/observing_conditions-adaptation:rw" observing-conditions-adaptation:cpu python -m research.observing_conditions.adaptation_runtime --output /workspace/artifacts/observing_conditions-adaptation/linux-runtime.json
```

后续优先保留小数据与固定参数修正，单独补充初始观测和跨夜过程；用新校准/验证场景检查范围与概率，再开启新的独立测试。树模型值得继续比较。当前选中的候选权重只用于研究，不标为正式可靠模型。

## 补充：树模型的运行导出

树权重另导出为 LightGBM 原生模型文本，避免运行时读取不同版本的 Python 训练对象，以及重复计算档位概率；原始权重、校准与测试选择未改变。1,024 条原预测与原生导出在 Mac 上逐值相同；跨 Mac/Linux 的最大差异为 {'gru': 3.5762786865234375e-07, 'tree': 0.0}。运行镜像补充了 `libgomp1` 依赖。

最新同输入单线程修正测速：Mac GRU p95 约 3.0 毫秒、树约 35.3 毫秒；Linux x86 转译环境约 16.6/34.5 毫秒。均包括修正归一化/校准，排除共享公式与基础模型特征构建；速度只代表本机实验。Linux 树入口使用原生文件，不依赖 sklearn 对象反序列化。
