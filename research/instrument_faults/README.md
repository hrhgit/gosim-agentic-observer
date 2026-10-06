# 仪器故障研究

独立判断“收到一次曝光反馈后，当前是否存在尚未修复的仪器故障”。正式智能体保持原样。本目录只读取正式卡 A–D 的公开输入；环境、事件和观测安排为自行生成。它们不是比赛隐藏数据。

## 运行

从项目根目录执行。依赖由 `uv.lock` 锁定；需要 Python 3.12。小型网络使用本机 MPS，其他模型使用 CPU。

```sh
uv sync --project research/instrument_faults
research/instrument_faults/.venv/bin/python -m pytest research/instrument_faults/tests -q
research/instrument_faults/.venv/bin/python -u -m research.instrument_faults run --workers 2 --await-weather
```

完整流程：8 个开发场景和官方评分对照 → 512 个主场景加 64 个外部场景 → 完整独立无天气预测模型（训练、校准、冻结后测试）→ 冻结已通过验证的天气模型 → 保存曝光前天气预测和连续反馈 → 受控比较是否加入天气预测 → 四种完整模型训练、独立校准 → 验证集申报模拟和选择冻结 → 删除输入组对照 → 最终与外部测试。`--await-weather` 等待上游实际产物，不设置超时，也不使用真实天气答案替代预测。

天气入口默认是 `artifacts/observing_conditions/models/selected/`，可用 `--weather-model` 指定同一格式的已选模型。缺少模型或 `validation_reliable=false` 时不会开始正式故障训练。天气模型目录及训练清单会被复制和校验；依赖改变必须使用新的 `--output` 重建故障实验。

完整实验规模较大。`_state.json` 和 `REPORT.md` 显示实际阶段和覆盖数；未结束的实验不会产生“已完成”或“已通过”的结论。已经完成的场景、候选训练和申报模拟可恢复，成功案例不重复执行。

分阶段运行：

```sh
research/instrument_faults/.venv/bin/python -m research.instrument_faults prepare --pilot --output artifacts/instrument_faults-development
research/instrument_faults/.venv/bin/python -m research.instrument_faults audit --output artifacts/instrument_faults-development
research/instrument_faults/.venv/bin/python -m research.instrument_faults prepare
research/instrument_faults/.venv/bin/python -m research.instrument_faults features
research/instrument_faults/.venv/bin/python -m research.instrument_faults train
research/instrument_faults/.venv/bin/python -m research.instrument_faults select
research/instrument_faults/.venv/bin/python -m research.instrument_faults ablations
research/instrument_faults/.venv/bin/python -m research.instrument_faults test
research/instrument_faults/.venv/bin/python -m research.instrument_faults report
```

最终测试入口要求 `selection.json` 已冻结，并核对模型、天气和模拟代码的校验值。测试集不用于调参。单独重新运行 `train` 改变产物后，旧的选择文件不能用于测试。

### 完整独立模型：不用观测质量预测结果

`benchmark.py` 独立完成训练、选参、校准、模型冻结和最终/外部测试，无需天气模型权重或预测结果。它接收实际反馈、公开目标和曝光信息、已收到的公告/预报，以及公开公式计算的几何信息；不使用常数预测或真实天气答案作为输入。由逐目标得分、亮度、时长及公开程序倍率整理的“表观响应”仍混有天气、程序不匹配和得分封顶的影响，不能解释为仪器效率。

```sh
# 完整独立模型；随场景生成整理输入，全部训练/验证/校准场景完成后才开始训练
research/instrument_faults/.venv/bin/python -u -m research.instrument_faults.benchmark run --await-data
# 真实天气模型已冻结、逐曝光预测与对应输入生成后，进行受控比较
research/instrument_faults/.venv/bin/python -u -m research.instrument_faults.benchmark compare
```

完整独立模型存放在 `artifacts/instrument_faults-feedback/`；受控比较存放在 `artifacts/instrument_faults-weather-comparison/`。两条过程始终按完整场景归组。原来 `feedback_only.py pilot` 的 8 场景检查仍保留在 `artifacts/instrument_faults-feedback-pilot/`，其概率未校准且存在开发场景安排限制，不作为完整模型成绩。

完整流程会在等待天气模型之前自动完成这份独立模型。它比较逻辑回归和提升树，沿用批准方案中的参数范围、场景等权重、训练集补齐和独立校准规则。按验证集分类损失选择单模型，固定 0.5 判断门槛，冻结后才使用最终和外部测试。独立抽查 200 条训练/验证/校准答案，报告准确率、故障漏判与误判比例、概率平方误差、排序能力、概率分组误差，以及整场景误差区间和分卡/天气/地震/强度/时长/密度分项。这些是当前故障分类指标；此独立实验不计算模型执行申报的收益。

预测入口为 `benchmark.Predictor(model_directory).predict(feedback_state, now_utc, saved_public_inputs)`，`feedback_state` 使用 `feedback_only.FeedbackEvidence(scoring_config)`。曝光记录使用 `targets`（公开目标字段），不包含 `predictions`。`saved_public_inputs` 的字段固定为 `issued_at_utc,observe_index,context,candidates`，保存曝光前由公开公告/预报及几何整理得到的向量，顺序见 `observing_conditions.features.CONTEXT_NAMES` 与 `CANDIDATE_NAMES`。版本返回真实模型权重校验值及 `weather_model=null`，无需天气模型预测。

未来的主要比较在这份模型的完整输入后，仅追加真实观测质量预测、参考得分差异及相关历史；原有输入逐行保持一致。两个版本使用相同的逻辑回归/提升树候选、参数预算和数据划分，各自按验证集合选择、独立校准，并在完全相同的测试反馈与答案上比较。`comparison_protocol.json` 在测试前保存方案。配对比较核对场景/过程/曝光编号与答案完全相同，按完整场景重抽样 2000 次，报告概率平方误差、分类损失和准确率的改善及 95% 区间；负值或跨过零的结果保留，不预设天气预测有效。四种完整故障模型的申报模拟仍按后面的方案独立完成。

## 数据和公式

每张卡的训练/验证/校准/测试场景数为 80/16/16/16；各集合的无/一次/三次故障比例为 25%/50%/25%。故障发生方式与地震开关交叉平衡，种子与天气模型所有集合完全分开。外部四组各 16 场景。D 卡保留偏移和数据撤销。故障效率 0.4–0.7，弱故障外部组 0.75–0.90。

每个场景有分散和集中两条全过程。每夜 8 个均匀候选时刻，稀疏外部组为 2 个；曝光时长为 60/300/900/1800/3600 秒等概率。开始时刻取整秒，重叠候选顺延，夜末由官方规则截断；跳过候选写入 `skipped-*.json`。集中观测每三次重复位置，程序循环 DARK/BRIGHT/BACKUP。

分散过程不申报；集中过程在曝光顺序的 25%、50%、75%、100% 位置，分别在对应曝光前申报一次。这样第四次仍在可观测时段内，不落在官方已经退出的季末之后。申报时机不读取故障状态。

`public-records-*.jsonl.gz` 保存每次曝光的逐目标公开信息和之前保存的预测，包括预测时刻、实际时长、目标位置和天气版本。`features.npz` 是每曝光一行的 206 个数值。卡号、目标号和种子仅用于对应记录，不作为特征。

参考得分为：

```
权重 × min(亮度 × 实际整数曝光时长 × 预测 Q_env / 公开归一化常数, 1)
     × [不匹配倍率 + 对应档位概率 × (匹配倍率 − 不匹配倍率)]
```

这是比较用的近似值，不是精确期望得分。预测质量允许大于 1；档位来自独立的三个概率。下界比较值使用 q05 与最小公开程序倍率，不能解释为得分的校准区间。参考值接近零时，比例为缺失，并保留缺失标记。

质量平均使用实际经过的完整时间；公开评分公式中的时长采用官方整数秒。`physics-*.npz` 把修复前后的贡献分开储存，每次模拟根据当前修复时刻重新计算后续反馈、最高分和结算，绝不回放固定得分。

答案在反馈到达时读取官方事件和修复状态；曝光中途发生故障，仍按当前状态标注。真实环境、倍率、档位、事件边界和偏移只留在评分与答案侧。数值分类器仅接收特征数组，没有读取环境文件的接口。

## 预测入口

训练完成后：

```python
from research.instrument_faults.predict import Predictor
p = Predictor('artifacts/instrument_faults')
state = p.state(public_scoring_config)
state.messages(already_delivered_messages, now_utc)
state.observation(saved_public_exposure_record)
result = p.predict(state, feedback_time_utc)
```

逐曝光记录的字段见 `core.Evidence.observation`。顶层字段固定为 `observe_index,start,end,night,duration,program,altitude,azimuth,predictions,result`。每项预测包含 `target_id,target,prediction,issued_at_utc,duration_seconds,weather_version`；`target` 使用公开亮度、权重和坐标；`prediction` 的顺序为：

```
mean_quality,q05,q50,q95,usable_fraction,severe_block_probability,p_dark,p_bright,p_backup
```

`weather.FrozenWeather` 复用上游 `Predictor.numeric` 与同一公开输入整理函数。曝光前生成预测；结束后只能与保存的这份预测比较。预测必须早于或等于曝光开始、对应相同时长和固定天气版本。任何曝光后公告不能重算过去的预测。

输出：

| 字段 | 含义 |
|---|---|
| fault_probability | 校准后的当前未修复故障概率；不可判断时为 null |
| evidence_exposure_count | 最近 30 次曝光里可比较的曝光数 |
| evidence_target_count | 其中有反馈、预测且参考值可比较的目标数 |
| direction_count | 覆盖的八方向数量 |
| last_evidence_age_seconds | 最新可比较曝光的年龄 |
| status | ready、limited_evidence、no_observation_evidence、weather_unavailable、confirmed_current_state |
| versions | 故障权重、天气权重和输入格式版本 |
| timing | 输入整理和推理时间 |

不足三次曝光仍可返回概率，但标为 `limited_evidence`。没有可比较目标或天气预测不可用时返回 null，不能按“无故障”处理。申报反馈只在同一时刻确认概率 0；时间推进后继续依靠证据。正确修复清除旧证据；撤销消息移除对应曝光并重建当前历史。过去已经保存的预测与输出不改写。

也可通过 JSON 一次传入历史，按时间恢复状态：

```sh
research/instrument_faults/.venv/bin/python -m research.instrument_faults predict --input public-request.json --result result.json
```

请求仅接受 `now_utc,scoring,weather_version,observations,messages`。模型输出概率；是否申报由独立评测规则决定，没有接入正式智能体。

## 模型和选择

逻辑回归、300 轮提升树、300 棵随机森林和 64/32 层小网络使用同一批输入和完整场景划分。提升树直接处理缺失；其他模型只从训练集计算补齐、缺失标记和标准化。每个场景样本总权重相同，不强制正负配平。所有参数和初始化数量按已批准方案执行。关闭自动随机拆分提前停止；网络按完整验证场景损失选择轮次。

参数先按验证场景平均分类损失选择；随机森林和网络复验三个初始化，交付一个模型。概率在独立校准集拟合 sigmoid。提升树的缺失处理见 [scikit-learn 官方文档](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingClassifier.html)，独立校准要求见 [官方校准说明](https://scikit-learn.org/stable/modules/calibration.html)。

申报比较不申报、简单规则、四个模型和真实状态理想参照。每三次新证据才考虑一次申报，无论这次是否申报，都不会复用这批证据。免罚和扣分阈值仅用验证集选择，先满足误报目标，再选平均得分最高；全部组合未满足目标时明确记录失败及实际采用的组合。模型收益相差不超过每场景 5 分时选推理更快者。

发现比例和延迟的分母取自同一不申报过程，避免修复后改变可观测机会。误差范围按完整场景重复抽样 2000 次；两条过程一起保留。概率分成十组后的误差按样本数加权。CPU 单线程推理包括缺失处理和概率校准，另报输入整理时间。

## 先做小规模比较

按用户最新要求，完整生成与百万条记录训练已暂停，成功数据和旧模型保留。当前先用 20、40 个训练场景，四张卡数量相同，较小集合包含于较大集合；同时覆盖无故障、一次和三次故障。验证固定为原定 64 个完整场景，两条观测过程仍归在同一场景。

```sh
research/instrument_faults/.venv/bin/python -u -m research.instrument_faults.learning_curve --sizes 20 40 --workers 4 --threads 2
```

四个进程同时运行两个规模的逻辑回归和提升树，每个进程最多两个 CPU 线程。固定参数比较数据规模，不同时扩大参数搜索。仅加载训练与验证输入；不使用校准、最终或外部测试答案。此阶段概率未经独立校准，0.5 判断门槛固定，结果不是最终成绩，也没有模拟申报收益。

产物在 `artifacts/instrument_faults-smallscale-4cpu/`，包含场景清单、固定参数、线程安排、训练时间、验证结果和模型文件。完成的组合可恢复，不重复训练；改变代码或配置须使用新目录。`--sizes` 也支持显式指定 80、160、320，但不会在当前小规模实验后自动扩大。

## 不训练模型的规则参照

```sh
research/instrument_faults/.venv/bin/python -u -m research.instrument_faults.rules --workers 4
```

使用同一套 64 个验证场景，比较最近三次平均响应偏低、最近三次相对最近三十次明显下降，以及没有当前天气干扰和最近四夜地震公告时才标记偏低。每类三个门槛在代码中固定，全部结果公开。输入仅为现有实际反馈和公开信息，不接收观测质量预测、真实天气、故障数量或种子。缺少证据时不发出故障标记，但计算漏报仍包含这些故障状态。

产物在 `artifacts/instrument_faults-rules/`，包含固定规则、64 个场景的结果、按完整场景抽样的区间和与小规模模型的同口径比较。没有读取最终/外部测试，没有模拟实际申报及修复。突降规则可能只在故障发生时短暂报警，持续状态识别率低不能证明故障事件发现率低；是否能及时修复并增加得分需要单独运行申报模拟。

## 与基线智能体规则的同场景比较

```sh
research/instrument_faults/.venv/bin/python -u -m research.instrument_faults.baseline_comparison --workers 6
```

把 `agents/claude-baseline-v1` 的环境估计器（`core/conditions.py` 的 `Sky`，原样导入、不改代码）放到同一套 64 个验证场景上，和 40 场景的逻辑回归、提升树比较。分两部分：

- 逐曝光判断：沿用训练数据的申报安排（分散过程不申报，集中过程固定四次），按基线智能体主循环的顺序把公告和每次曝光反馈喂给 `Sky`。基线的判断取它的内部状态“正在怀疑仪器，且估计效率低于下降前的 78%”；指标、场景等权和可比较反馈的筛选与小规模模型表一致。回放得到的答案必须与已保存的分类答案逐条相同，抽查场景的分类输入也必须能逐列复现。
- 闭环申报：每种方法自己决定何时申报，报对后其后的反馈和得分按修复后重新计算。基线用它自己的 `want_report`；模型沿用原方案的规则（每三次新的可比较曝光判断一次，免罚/扣分两个概率门槛）。门槛网格与原方案相同，在 64 个未参与 40 场景训练的训练集场景上选定后冻结，再用于验证场景；另报一份直接在验证场景上挑门槛的事后参照，只说明上限、不当作成绩。基线规则也在选门槛的那 64 个场景上跑一遍：规则没有在任何这些场景上调过，对它是第二批独立场景，对模型则偏乐观。模型的误报集中在少数场景，报告同时给出最差场景的得分变化。

固定安排里相邻曝光间隔 60–80 分钟，基线“只和一小时内读数比”的同夜突降判断不会生效，所以另跑一个只把该常数放宽到 2 小时的版本；这个改动在看结果前定好，没有按结果调整。基线估计剩余收益时用固定安排剩余的曝光秒数代替剩余夜间时间。基线在自己比赛过程里使用的信标目标、DARK 探测和缩短曝光在固定安排里都用不上，结论只适用于这种安排。

产物在 `artifacts/instrument_faults-baseline-comparison/`：`protocol.json`（代码、模型和场景校验值）、`selection.json`（门槛选择）、`scenes/`（逐场景结果，可恢复）、`nowcast/`（基线在每次曝光前对各目标的质量和档位估计，供 `research.observing_conditions.nowcast_comparison` 使用）、`results.json` 和 `REPORT.md`。没有读取校准、最终或外部测试。

## 档位证据与实际申报优化

```sh
research/instrument_faults/.venv/bin/python -u -m research.instrument_faults.report_model --workers 4
```

复用 40 个训练场景；另取 64 个未被该模型训练使用的训练场景选择申报门槛，之后冻结，在原 64 个验证场景检查。验证集已经用于前期诊断，因此这次改进检查不能称为最终独立成绩。最终、外部测试继续保留。

从公开目标得分、权重、程序倍率和几何量提取匹配、错配、无法判定及天气档位对应的上下界线索；没有读取真实档位。分数高于错配最大得分可支持匹配，接近错配上限的读数则仍可能与匹配后的部分得分混淆，不能把推断当作真实档位；上下界也依赖这份判断及曝光平均近似。另保留基线从这些反馈推断的天气、效率、下降前效率、持续异常和证据状态。误报反馈确认当前仪器没有故障，清除旧故障证据并重新整理天气解释；复用基线的持续异常检查和误报后冷却条件，避免旧反馈不断触发申报。

代价权重只在训练侧使用：故障样本按正确奖励和预计修复收益加权，正常样本按官方扣分误报成本加权，每个场景总权重仍相同。模型分数是申报倾向，**不是校准故障概率**。选择依据为实际申报修复后的得分，先满足误报与扣分误报目标；没有候选达标时如实记录。修复后后续反馈必须重新计算。包括旧模型、旧模型只换申报处理、新模型、以及新模型不加申报保护的对照。

产物位于 `artifacts/instrument_faults-report-model/`；固定场景、代码和费用版本在 `protocol.json`，门槛冻结在 `selection.json`，每次实际申报与修复记录位于 `simulations/`。收益估计沿用固定安排里剩余的曝光时长与近期得分速度，不等同正式智能体将来的观测收益。正式平台 2 核、2 GB 等限制见 `docs/participant-runtime-limits.zh.md`；本轮仅为本机研究，没有把训练数据或研究环境打包进正式参赛项目。

## 产物

代码在本目录；数据和产物在 `artifacts/instrument_faults/`，开发检查单独在 `artifacts/instrument_faults-development/`。

- `manifest.json`、`generation_code.json`、`runtime.json`：来源、种子、划分、代码及依赖。
- `audit.json`、`saved_label_audit.json`：官方运行器对照、200 条答案核对；恒定预测只用于评分程序诊断。
- `weather_model/`、`weather_dependency.json`、`weather_training_manifest.json`：真实天气模型的冻结副本和校验信息。
- `scenarios/<id>/`：独立环境、观测计划、逐目标反馈、天气预测、汇总输入及答案侧记录。
- `models/`：候选、复验、校准、删除输入组的对照与最终 `selected/`。
- `selection.json`：验证集选定并冻结的参数和规则。
- `simulations/`：每条全过程的申报决定、修复后结算、发现延迟和输入耗时。
- `report.json`、`ablation_comparison.json`、`REPORT.md`：比较结果、最终与外部四组测试、验收目标实际达到情况。

准确率和收益结论仅适用于这些自行生成数据与固定安排。目标未达时如实保留；不反复利用最终测试调参。
