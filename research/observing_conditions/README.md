# 独立观测条件预测模型

传统神经网络，预测单个天空位置的一次曝光条件。返回质量均值与分位数、有效时间比例、严重受阻概率、DARK/BRIGHT/BACKUP 概率。模型不生成文字建议、不制定策略，不判断隐藏仪器故障。

当前信息提炼版本使用 `information_predict`：返回固定天空分区、逐位置预测、公开依据和可信度证据。新实验见文末“信息提炼版本”。下面的早期训练、排序和筛选实验保留为历史记录；原入口与权重仍保留。

训练来源是 `cards/a` 至 `cards/d` 的正式公开输入，加上官方生成器生成的新天气。所有天气明确标注为自行生成，不代表正式比赛隐藏数据。正式卡不改动，不使用或恢复官方练习卡。

最新连续观测反馈实验见 [RELIABILITY.md](RELIABILITY.md)：主网络冻结，区分上一夜历史与当晚反馈，只训练一次1109参数小头，新增完全／部分／全程可观测的概率。16个新场景核查已有局部收益，但首次观测和范围保守问题仍未完整达标；独立入口为 `reliability_predict`，未替换正式智能体。

## 环境与运行

从项目根目录运行。Python 3.12，依赖固定在本目录 `uv.lock`，环境在本目录 `.venv`。

```sh
uv sync --project research/observing_conditions --python 3.12 --locked
uv run --project research/observing_conditions python -m research.observing_conditions generate --pilot --output artifacts/observing_conditions-pilot --workers 4
uv run --project research/observing_conditions python -m research.observing_conditions audit --output artifacts/observing_conditions-pilot
uv run --project research/observing_conditions python -m research.observing_conditions run --workers 4
```

分阶段运行可用 `generate`、`audit`、`train`。生成按完整场景恢复；训练每轮保存权重、优化器、随机状态及进度，重跑同一命令恢复尚未完成的任务，跳过已完成任务。参数或官方输入变化会拒绝混用旧结果，应指定新的 `--output`。

可选地在生成、核对与设备测速完成后运行 `python -m research.observing_conditions.pretrain`，让 TCN 在第二个本地进程训练，与主进程的 CPU 训练重叠。两者使用同一训练函数、文件锁和完整检查点，已完成任务会复用，不会重复写入。

默认完整数据：320 个主场景（每卡训练 56、验证 8、校准 8、测试 8），加 32 个天气变化测试和 32 个新站点测试。每场景 128 组，每组 32 个候选，共 1,572,864 条。每组 24 个正式目录位置和 8 个天空均匀抽样位置。

主场景使用正式卡原始日历的全部观测夜与时段，日历文件逐字节保持一致。天气调用官方的事件、天气和公告生成函数，直接传入该公开日历，避免根据说明页舍入后的站点坐标重新计算时间。生成器结束日期按不包含该日期处理，因此配置填写最后观测日期的次日。新站点是自行设定的纬度 −10°／经度 30°和纬度 35°／经度 −100°，日期 2027-01-01 至 2027-06-30，候选目录沿用正式公开目录，仅重新计算可观测性。

## 输入、答案及模型

数值输入：当前公开公告、最近 32 个可观测时段公告及实际时间间隔、持续事件状态、一次公布后持久保留的地形消息、已发布且覆盖候选夜的预报；候选开始/中间/结束位置、最低高度、时间、申报/实际时长、月亮及大气几何因子；公开站点与计分参数。

天气种子、卡片编号、目标编号、精确天气、事件强度、隐藏边界、未来公告以及观测得分不进入特征。数据中的场景和卡片标签仅用于划分及分项评测。预处理只在训练集拟合。

答案 `Q_env` 使用官方评分公式，仪器效率统一为 1，不计仪器故障及地震造成的效率下降；保留天气与方向事件影响。Q 可以大于 1。`U` 是没有天气关闭或方向关闭的时间比例，严重受阻定义为 `U < 0.5`。档位调用官方独立档位公式，不由 Q 推断。夜末截断用实际时长，整段最低高度由公式筛选。

模型：MLP（128/128/64）、两层 64 单元 GRU、4 个 64 通道因果时间卷积块。共享公开历史表示对同组候选只计算一次；MLP 的共享线性拆分与拼接后全连接数学等价，不减少训练信息。每批 16 组 ×32 个候选 =512 条。

比较学习率 0.0003／0.001，固定训练子集 20 轮调参，选定参数后 3 个初始化种子各完整训练 60 轮。损失：质量均值平方误差、分位数损失、有效比例平方误差、二类及三类分类损失，五项等权。保存综合验证误差最小的轮次。

**设备选择是对原方案的实测调整**：先测本机 CPU 单线程与 MPS 的完整训练步骤，各架构选择较快设备，保持 float32、数据量、批次和轮数。速度记录在 `device_benchmarks.json`，不会因超时而缩短训练或降级输出。

## 评测与选型

两个参照是常数统计预测和统计查表。查表使用当前事件、持续保留的已公布地形方向、相对粗方向（中心 ±45°）、15° 高度分箱、预测距离及季度；质量按已知几何因子调整，稀少单元以 100 条等效全局样本进行平滑。此粗方向仅是统计分箱，不宣称真实受影响边界为 ±45°。

验证集按整场景一分为二，一半拟合临时校准，一半比较可靠性。可靠性合格者优先，再比较质量 MAE；差距不足 2% 时选择 CPU 推理更快架构。如果没有架构通过可靠性门槛，仍保存验证最优研究模型，明确标记未通过可靠性检查。

最终概率及区间参数只在独立校准集拟合；最终测试不用于调参。三个种子输出平均后，分类使用温度缩放、质量区间依据分位数残差扩大。模型差异不直接冒充预测区间，时序相关与分布变化不满足独立逐条保证。

主测试报告 MAE、总体相对误差、有效比例误差、Brier、各档 F1、概率可靠性、区间覆盖与区间评分、同组位置选择损失。按卡、事件、高度、曝光长度、预测距离细分；按独立完整场景进行 2,000 次重复抽样。外测报告天气参数变化和新站点；还运行四次固定架构留卡实验与删除历史／预报的两次实验。留卡实验不重新选择架构与学习率，训练、预处理、校准排除留出的卡。

验收目标与计划一致。未达标逐项记录，不在最终测试后反复调整并继续称其为独立测试。CPU 延迟是预热后的单线程 1,024 候选网络集成纯推理，特征处理、概率校准及完整请求耗时另报。

## 预测接口

```sh
uv run --project research/observing_conditions python -m research.observing_conditions.example
uv run --project research/observing_conditions python -m research.observing_conditions predict --input request.json --result prediction.json
```

示例命令生成 `artifacts/observing_conditions/example_request.json`，候选位置取自自行生成场景，公告与预报按决策时刻过滤，不读取质量答案。预测命令的 `--input` 可使用该文件。

请求结构：`initialize` 是官方初始化消息的 payload（含 site、scoring、survey.nights、instrument）；`now_utc`；`bulletins` 和 `forecasts` 是已经收到的公开消息历史；`candidates` 是候选数组，每条包含 `candidate_id`、`ra_deg`、`dec_deg`、`start_utc`、整数 `duration_seconds`。

候选开始时间是现在、+900、+1800 或 +3600 秒。历史中包含未来消息时会被时间过滤；模型不读取天气文件或训练答案。出界或低于最低高度的候选返回 `eligible:false` 及原因，不返回虚构的质量预测。

有效候选返回 `mean_quality`、`q05`、`q50`、`q95`、`usable_fraction`、`severe_block_probability`、`p_dark`、`p_bright`、`p_backup`。质量区间覆盖率目标为 90%，不是单个点的正确概率。响应同时含模型版本、质量定义、训练来源和输入处理／预测时间。

响应中的 `final_test_passed` 和 `acceptance` 是已经完成的最终验收结果；`validation_reliable` 仅是验证集阶段结果。研究模型即使未通过最终验收也保留权重与客观预测，必须结合报告中的限制使用。

完整产物保存在已被 Git 忽略的 `artifacts/observing_conditions/`：manifest、原始自生成天气、数据、训练记录、最佳模型、校准参数、逐模型预测、JSON／Markdown／HTML 报告。第一轮不改智能体、不进行线上提交。

## 第二轮：减少智能体判断工作

本轮固定 MLP、种子101，每个新方案只训练一次。4次训练各20轮，复用第一轮模型与统计查表；不重新训练GRU/TCN，不重复不同初始化。比较简短历史、公式与环境分工、增加独立天气、真实天气诊断（仅研究）。原训练集每场景抽16组作为固定筛选子集，不能把不同训练预算的结果当作特征改进的严格因果证明。

规则按验证集的风险和节省比较数量选择，冻结后才生成新测试场景。新32训练场景、56测试场景；优先清单另冻结规则并使用32个新场景。来源均为自行生成，正式输入保持原样。入口仍不接入智能体、不自动执行动作。

```sh
uv run --project research/observing_conditions python -m research.observing_conditions.improvement
uv run --project research/observing_conditions python -m research.observing_conditions.priority
uv run --project research/observing_conditions python -m research.observing_conditions.benchmark_batching
uv run --project research/observing_conditions python -m research.observing_conditions.finalize_assistance
uv run --project research/observing_conditions python -m research.observing_conditions.advisory_predict --input request.json --result prediction.json
```

结果在 `artifacts/observing_conditions-v2/`。首轮完整产物不覆盖。严格排除在较强天气变化检验中失败，交付入口关闭该功能；直接推荐单个位置也关闭。仍返回逐候选客观预测、公式不可用标记、条件排名和模型/公式的优先清单。

“好位置”指有效时间比例≥0.5、质量≥同组最好95%。清单从32缩小至4，潜在减少87.5%候选比较，但仅适用于目标价值和任务约束已筛选、相同开始时间和曝光时长的候选。不是测得的智能体耗时减少，也不证明清单外位置不可用。公式参照在本批测试优于神经网络排序，当前数据未证明网络清单有额外价值。

响应新增 `formula_excluded`、`suggest_exclude`、`suggest_priority`、`condition_rank`、`in_priority_list`、`formula_condition_rank` 和 `known_geometry_factor`。各组返回模型优先清单及 `formula_priority_reference_candidate_ids`。`assistance_acceptance` 按能力记录经验检验结果；`automatic_actions_enabled`始终为false。模型置信度不当作确定性证明，90%质量区间不代表排名正确概率。小场景数、输入分布变化、目标价值和真实智能体调用仍限制结论。

批量位置计算已应用到原Predictor：相同时间只算一次月亮位置，重复候选复用公式结果，历史状态按时间过滤。`batching_benchmark.json` 保存同权重、同请求的改进前后数值核对和完整处理耗时，包括1024个独立位置及4组时间，避免只用重复位置声称加速。

## 第三轮：非神经网络模型

比较 ExtraTrees（多棵随机树共同预测）、LightGBM（逐步修正误差的树）及 LightGBM 专门排序模型。每份不同输出只拟合一次，不搜索参数、不重复初始化，神经网络参照复用首轮完整模型和第二轮简短历史模型的已有权重。

使用同一份73,728条训练记录及相同公开信息，训练集拟合常量特征筛选；验证集选定模型与规则，独立校准集调整概率和区间。冻结后生成32个新测试场景、65,536条候选。结果和权重单独保存在 `artifacts/observing_conditions-trees/`。6次拟合分别为一份多输出随机树、四种不同输出的提升树、一份排序树，合计约59秒。

```sh
uv run --project research/observing_conditions python -m research.observing_conditions.traditional
uv run --project research/observing_conditions python -m research.observing_conditions.tree_review
uv run --project research/observing_conditions python -m research.observing_conditions.tree_verify
uv run --project research/observing_conditions python -m research.observing_conditions.tree_predict --input request.json --result prediction.json
```

完整实验选定排序树；主测试缩到1个位置没有丢失好位置，新站点丢失率1.03%，超出1%要求，因此交付入口关闭模型缩小清单、严格排除和代选。没有在看过最终测试后改选另一模型。固定保留4个位置的比较另报，不能据此修改规则并声称通过独立验收。

树模型未全面优于首轮完整神经网络：主测试质量平均绝对误差约0.2264（随机树）与0.2282（完整网络），完整网络在较强天气变化和新站点的质量误差更低。提升树的受阻概率误差有所降低，但新站点质量区间覆盖约79%，随机树约82%，均不足。结果仅限自行生成数据；场景抽样得到零错误区间也不代表零风险。

调用返回原有九项数值及独立 `ranking_score`；排序分数不能当作质量或正确概率。区间取独立校准残差，不把树之间的差异当作预测区间；`q50`是点预测加校准残差中位数，不保证是真实条件中位数。失败能力保持关闭，仍可查看全体位置的排序与公式参照清单。

macOS 上 PyTorch 与 LightGBM 自带的并行运行库可能冲突。上述 `tree_predict`、`tree_review`、`tree_verify`入口会在进程启动时使用同一份 PyTorch 环境内运行库，不改安装文件、不重训。直接在其他 Python 程序使用 `TreePredictor` 时，以 `python -m research.observing_conditions.tree_runtime 你的模块 ...` 启动；缺少启动配置会明确报错。问题背景见 [LightGBM 官方说明](https://lightgbm.readthedocs.io/en/stable/FAQ.html)。

## 对齐比赛：完整观测方案入口

一次observe是一个指向、同一曝光时间、一个共同声明的程序，以及各光纤分别对应的目标；这些目标必须在该指向的对应方格内。A/B/C/D的单次容量分别为16/25/9/100个目标。训练中的32个分散天空点是比较样本，固定保留4个不能当成完整比赛动作。

新增 `plan_predict` 接收官方initialize中的 `instrument` 和 `targets:{columns,rows}`、当前时间、公告/预报及 `observation_plans` 数组。每个方案包含 `plan_id`、`pointing:{alt_deg,az_deg}`、`assignments:{fiber_id:target_id}`、`start_utc`、`duration_seconds`；可带 `program`。开始时间仍支持现在或15/30/60分钟后，曝光60–3600秒，实际时长按夜末截断。容量读取公开仪器配置。返回全部方案及全部已分配目标，没有固定top4或top16方案限制。

正式目标目录A/C/D各30000个，B为50000个。当前可观测目标数量随时间、整段最低高度及任务状态变化；候选观测方案由智能体按指向、目标分配、曝光与程序组成，题目没有规定固定方案数。条件模型只收到智能体提交的候选位置或完整方案，不自动遍历整个目标目录。32是数据生成时的每组抽样数量，不是正式比赛候选上限。

```sh
uv run --project research/observing_conditions python -m research.observing_conditions.plan_example
uv run --project research/observing_conditions python -m research.observing_conditions.plan_predict --input artifacts/observing_conditions-plans/example_request.json --result artifacts/observing_conditions-plans/neural_prediction.json
uv run --project research/observing_conditions python -m research.observing_conditions.plan_predict --backend trees --input artifacts/observing_conditions-plans/example_request.json --result artifacts/observing_conditions-plans/tree_prediction.json
```

默认复用首轮完整MLP权重，也可复用树模型；无新增训练，原接口保留。示例用正式A目标及仪器、自行生成a-000公开公告，演示两个完整方案（16与15个目标）；示例指向按公开目标占用选取，不是智能体策略或官方得分。

逐目标返回质量均值/区间、有效时间比例、严重受阻及档位概率；保留目标ID、光纤ID、必观测标记及价值仅用于关联和公开公式计算，不进入模型特征。最低高度或夜外由已知公式标记不可用。方格匹配只检查无隐藏指向偏差时的名义位置，不能当成已经证明实际命中；模型仪器效率为1且不含隐藏故障。

额外按官方公开公式，给出预测质量对应的完成因子及基础得分估计、完成因子区间与必观测门槛的关系。它是将预测代入公式的估计，因得分有上限，不能当作严格期望得分；不计程序加成、旧最好得分、限时任务奖励或最终覆盖罚分。区间超过门槛不等于确定完成。

每个方案汇总已分配/名义有效/必观测目标数、预计至少有一半时间可用的目标数量、平均预测质量和基础得分估计；不将16个目标的概率相乘，不给未经验证的整次成功概率或总分区间。目标间天气相关，单目标90%区间不是整方案90%保证。所有天气排除、自动代选关闭；返回全部方案供智能体结合已有进度和目标价值判断。

### 排除结果与指标

已试过两类排除：按很高的受阻概率排除；按质量区间与其他位置比较，移出明显较差位置。第一类在第二轮较强天气测试排除166条，其中44条U≥0.5，误排率26.51%，交付关闭。第二类不等于物理不可观测，移除可用但相对较差位置也会影响必观测/限时任务，所以不能直接应用到不同目标或完整方案。

质量使用平均绝对误差（MAE）和总绝对误差/总真实质量（WAPE）；有效时间使用MAE，单位为比例点；严重受阻用概率与实际0/1结果的平方差平均（Brier）和概率分组偏差；档位用三类平均F1、概率误差；质量区间用覆盖率、宽度及区间评分。排除同时看排除比例、被排除却仍可用的比例及损失全部好位置的比例。减少候选不等于节省真实智能体时间。

新增完整方案入口已核查接口与公式，已有预测精度来自单目标位置测试。实际完整方案的分数损失、必观测完成率、任务奖励和智能体工作耗时尚未评估，不将旧32→4结果冒充这些成绩。

## 检查

```sh
uv run --project research/observing_conditions python -m pytest research/observing_conditions/tests -q
```

检查官方位置／最低高度一致性、未来公告不影响输入、地形状态持久、部分关闭、方向跨 0°、独立档位、网络梯度与输出约束、概率及指标。数据审计检查全部记录有限性和比例约束，并抽取 200 条对照官方评分函数、独立核算有效时间。

## 信息提炼版本：单次 GRU／MLP 比较与固定天空分区

本版提炼信息供智能体推理，返回全部区域或被查询位置。接口没有排序、前几名清单、天气排除或自动观测动作。公开事实由程序整理，概率及预测范围由传统神经网络预测，可信度来自独立场景实测。

训练复用224个原训练场景和32个已有扩充训练场景，每场景固定抽32组×32个位置，共262144条记录。GRU和MLP接收同样的公告历史、预报和位置特征，各单个初始化、各20轮，不进行学习率搜索或重复训练；采用既有本机速度测量更快的CPU单线程。分别实测约158秒和61秒。验证和校准各32个独立旧场景，每场景64组；归一化仅使用训练数据。开发集合复用的限制在protocol.json中记录。

输出为质量均值与5/50/95%分位数，有效时间比例均值与5/50/95%分位数，严重受阻概率，以及DARK/BRIGHT/BACKUP概率。质量不截断到1；有效比例与其分位数限制到0–1。训练使用两组分位数损失、二类与三类交叉熵，以及权重0.2的均值平方误差。验证按四项统计查表参照归一化后的分位数和概率评分选取轮次，架构选型先检查校准可靠性；选定GRU。

模型、概率校准与区域均值区间校准冻结后，新增32个场景：正常16、变化更大的天气8、两个自行设定的新站点8。位置测试65536条；区域测试每场景8次曝光、每次24区×9个代表位置。抽32个新位置和32个新区域代表位置直接核对官方评分及独立有效时间积分；质量最大差异小于2e-6。没有在最终测试后修改模型或校准参数。

方向分为N/NE/E/SE/S/SW/W/NW，高度由站点最低高度至90°分三层。每区3×3代表位置在方位和sin(高度)上均匀取样，各代表等面积。区域均值、受阻概率和档位概率只汇总整次曝光满足高度要求的代表位置，同时返回满足高度要求的面积比例估计。概率描述随机选一个合格位置的一次曝光，不是整区同时受阻的概率。区域内预测均值的差异与未来结果的不确定性分开；区域均值区间用区域均值误差单独校准，不能平均单点区间或按代表数量缩小天气误差。

新增12个场景中的单次曝光9点/36点采样敏感度检查；它只说明本次抽样变化，不证明9点可以覆盖每一种小范围事件。正式卡公开公告只给粗方向，不提供精确边界或强度。运行时不读取真实天气或事件文件。

本轮正常、天气变化、新站点的**总体**概率和范围指标达到初步目标；部分分项未达标，部分事件与卡片的独立场景不足，**完整可信度验收未通过**。报告不能用总体结果掩盖分项；返回的`reliability_evidence`包含分项失败、场景数量和适用限制。概率分10组，并以完整场景重复抽样报告实际频率误差范围。U大量落在0/1，连续网络原始区间对精确端点覆盖不足，校准后仍需检查覆盖与宽度。所有成绩限于自行生成数据。

```sh
research/observing_conditions/.venv/bin/python -m research.observing_conditions.information_train
research/observing_conditions/.venv/bin/python -m research.observing_conditions.information_predict --input artifacts/observing_conditions-information/example_request.json --result artifacts/observing_conditions-information/example_prediction.json
research/observing_conditions/.venv/bin/python -m research.observing_conditions.information_predict --input artifacts/observing_conditions-information/sky_request.json --result artifacts/observing_conditions-information/sky_prediction.json
```

已完成训练会复用权重；中断恢复只继续剩余轮次。产物保存于被Git忽略的`artifacts/observing_conditions-information/`，包括protocol、selection、frozen、训练记录、权重、预处理、校准、报告和真实入口输出。`sky_request.json`使用公开初始化、决策时间、公告、预报和`exposures:[{start_utc,duration_seconds}]`；每个曝光返回24区和216个代表位置。具体位置入口仍接受`candidates`数组并全部返回。新模型尚未接入正式比赛智能体。

## 与基线智能体估计的同口径比较

```sh
research/instrument_faults/.venv/bin/python -m research.observing_conditions.nowcast_comparison
```

在故障实验的 64 个验证场景上，对每次曝光里的每个目标，比较曝光开始前的两类预测：首轮 MLP 和信息提炼版 GRU（只看公告、预报和位置几何），以及 `agents/claude-baseline-v1` 规划时使用的估计（只看公告和此前曝光的反馈；天气水平 × 该目标的大气和月光因子 × 方向性折扣）。真实值是官方公式算出的不含仪器效率的质量和档位水平。两个模型直接读取这些场景已保存的公开输入，不重新训练；这些场景的种子与两个模型的训练、验证、校准和测试场景都不重合。

指标：质量平均绝对误差、总体相对误差、平均偏差、三档概率误差、档位判对比例，以及“按预测的档位概率给整次曝光选一个程序”实际拿到的平均倍率。每个场景作为一个抽样单位，重复抽样 2,000 次给出模型减基线的差异范围。有未修复故障时基线会把部分仪器损失记成天气，所以主要比较只用 16 个无故障场景，全部场景另报。

基线一侧的预测由 `research.instrument_faults.baseline_comparison` 在回放时保存，需要先运行它。固定安排里相邻曝光间隔 60–80 分钟，基线的估计比它在自己比赛过程里陈旧得多；这项比较不含曝光时长和选址，也不是比赛得分。结果在 `artifacts/observing_conditions-baseline-comparison/`。

## 连续反馈修正小数据实验

新增公式／冻结基础模型／反馈修正三部分的实验，见 [ADAPTATION.md](ADAPTATION.md)。单次小 GRU 与 LightGBM、连续轨迹、公开反馈在线更新比较、独立测试与 Linux 资源证据已保存。最终验收未通过；下一夜及历史不足阶段仍需改进。没有接入或提交比赛智能体。
