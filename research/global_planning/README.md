# 全局规划：本机分层实验

只优化官方整场最终分数。模型输出行动概率和剩余收益；没有近期预测头。先模仿现有规划，再使用 PPO。当前代码覆盖官方环境适配、有限候选选择、微型训练、GRU/无序历史 MLP 对照、计分与信息边界检查、独立测试和阶段门槛。

第一轮已经运行，结果为**尚未证明有效，未扩大训练**。独立小场景相对原规则 +0.26%，穷举能力检查通过 2/3；选定权重来自模仿阶段。详见 [完整实测与未完成项](RESULTS-v1.md)。

## 运行

从仓库根目录运行，使用已有 Python 3.12 环境和 Node.js 22：

```sh
export GLOBAL_PLANNER_NODE=/absolute/path/to/node22
"$GLOBAL_PLANNER_NODE" agents/global-planner-v1/node_modules/typescript/bin/tsc -p agents/global-planner-v1/tsconfig.json
research/observing_conditions/.venv/bin/python -m pytest research/global_planning/tests -q
research/observing_conditions/.venv/bin/python -m research.global_planning micro \
  --node "$GLOBAL_PLANNER_NODE" --out artifacts/global-planning-v1
research/observing_conditions/.venv/bin/python -m research.global_planning status \
  --out artifacts/global-planning-v1
```

依赖尚未安装时，在智能体目录用 Node.js 22 的 npm 执行 `npm ci --include=dev`。本机默认 Node 路径仅是已安装工具的便利值；可以通过参数或环境变量明确指定。提交配置没有引用该本机路径。

`micro` 先核查官方引擎，并用 observed-timeline-v4 的 A 卡运行现有规则与两个冻结模型，检查真正发生了观测且日志没有加载错误。然后测 CPU/MPS、小场景单/双进程、收集 32 个教师轨迹、8 个验证场景，训练 GRU 和无序历史 MLP。各 20 轮模仿、10 轮 PPO，每批 4 个完整场景、更新 4 遍，第 5/10 轮验证。只根据验证集选择结构与权重，保存 `frozen.json` 后才生成 16 个能力测试场景。正式测试和压力测试仅登记划分，尚不生成。

## 行为与信息边界

- `engine_worker` 单独运行原始、经校验的官方引擎。只有这个进程读取真值；训练器收到公开初始化和反馈，以及结束后的评分标签。隐藏的 `organizer_only` 字段不会发出。
- TypeScript 副本在原候选范围内返回全部安排，包含原规则选择。候选预览不推进曝光账本，也不消耗观测模式探测状态。选中后生成的实际动作必须与预览一致。
- 故障处理和基础校准继续由原代码执行。强制动作保留在完整奖励时间线上，但不伪装成模型选择。
- 每步公开科学累计分数之差作为中间奖励，最后一步补齐官方最终结算差额。因此请求、必做、均匀度、报告和撤销既不漏算也不重复。所有后续收益都使用 `gamma=1`，梯度分段不截断回报。
- 数字特征排除场景名称、种子、候选 ID、教师标签、未来消息及真实结算。允许使用由公开输入算出的规则估计、剩余机会和条件模型输出。完整区域信息先汇总为统计特征，无额外候选截断。
- GRU 和 MLP 使用同一批信息；MLP 汇总截至当前的全部编码历史，保留当前状态，但不按历史顺序递推。候选网络共享参数，不依赖固定数量或位置。
- 当前条件模型只支持未来 15/30/60 分钟等已声明范围。夜外返回不适用，不当作零质量预测；当前时刻仍使用已有公开反馈估计。

## 复现与阶段控制

`protocol.json` 固定源码、官方引擎、配套模型来源、数据划分、版本与训练参数。相同场景的所有策略轨迹属于同一集合。修改源码或设置后需使用新实验目录；不能混用旧结果。

每场保存动作、公开反馈、模型输入、选择、后续回报、官方分项和时间统计。错误单独保存为 `error.json`，不会作为成功样本。`resume.pt` 包含模型、优化器、归一化、随机状态和训练位置；场景独立抽样随机数使中断后能复用已完成轨迹。重新运行相同命令继续剩余部分。

微型门槛要求：完整结束；超过只看眼前科学分的对照；延期收益类案例有实际胜出；相对原规则不退步超过 1%；三项可枚举案例达到当前候选树的最优值。枚举只用于检验，不向模型提供隐藏答案，也不声称是连续动作空间的全局最优。

达到微型门槛才进入完整赛程。随后依次检验区域与时间选择、具体目标、公开信息下的搜索。完整赛程扩大门槛为平均至少 +1%，每卡退步不超过 1%；最终还需三个初始化、独立压力测试及 Linux 运行检查。**这些后续层次不能用本轮小场景结果代替，也不会在门槛未通过时自动启动。** 当前未实装的后续行为在 `stages.json` 和报告中明确列出。

训练吞吐量不启用比赛计时；正式入口另用官方 fair clock 核查，两者不混报。`stage0` 的 Mac 测速不是 Linux 服务器证据。已有四张本地卡属于回归材料，天气是假设；所有成绩都不是线上预测。

## 结果位置

默认 `artifacts/global-planning-v1/`：`protocol.json`、`stage0/`、`teacher/`、`gru/`、`mlp/`、`frozen.json`、`selected-test/`、`capability/`、`stage1-result.json`、`stages.json`、`REPORT.md`。权重在各结构的 `best.pt`；它可能来自模仿阶段，不能笼统称为 PPO 改进后的最佳权重。
