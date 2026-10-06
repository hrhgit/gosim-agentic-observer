# 全局规划第 2–6 层

这是一轮独立的后续实验。用户明确要求在第一层未通过后继续检查第 2–6 层；第一轮结论保留，第一轮权重不自动部署。

运行前用 Node.js 22 编译接入副本：

```sh
export GLOBAL_PLANNER_NODE=/absolute/path/to/node22
"$GLOBAL_PLANNER_NODE" agents/global-planner-v2/node_modules/typescript/bin/tsc -p agents/global-planner-v2/tsconfig.json
research/observing_conditions/.venv/bin/python -m pytest research/global_planning/tests research/global_planning/staged/tests -q
research/observing_conditions/.venv/bin/python -m research.global_planning.staged.experiment \
  --out artifacts/global-planning-v2 --node "$GLOBAL_PLANNER_NODE"
```

本机使用 CPU 单线程模型计算，并行运行两个独立模拟进程；先前小规模对照中，两进程的动作与得分一致，合计耗时比单进程短。不会用未完成赛程的分数训练。

可单独重做全自生成的小场景试点：

```sh
research/observing_conditions/.venv/bin/python -m research.global_planning.staged.small_experiment --out artifacts/global-planning-small-new
```

正式入口会先完成小场景，再按门槛决定哪些层次扩大到完整赛程。

正式训练/验证/独立测试/压力测试预先登记 16/8/16/8 个场景。完整正式公开目标与日历原样保留；新增天气、事件、请求由现有生成器制作，新增公告与预报放在 `generated_public`，不覆盖原公开文件。

先以每卡一场完整训练、一场完整验证作小规模试点，使用 2 轮模仿和 1 轮强化训练，参数在 `protocol.json` 预先固定。每批 A–D 各一场完整赛程、更新 4 遍。平均至少 +1%、每卡下降不超过 1% 才扩大到 16/8 场和 10 轮；未达到时仍继续比较其他层次的小规模版本，最终保留经过验证的较好方案。

- 第 2 层：原规划器的完整候选；原规则选择始终存在。GRU 和使用相同历史信息但不处理顺序的普通网络作对照。
- 第 3 层：所有具备观测几何条件的区域，再选原离散时长表中的曝光时间，可主动等待。原选择也保留；低天气预测不删除区域。同范围规则单独比较。
- 第 4 层：固定第 3 层的区域和时间策略，只学习模式和各光纤的目标。可选择同一光纤内的其他目标或空置。指向计算、微调、故障报告、基础校准继续由现有代码处理。
- 第 5 层：比较 16 次展开/两步和 64 次展开/三步。抽样只使用训练轨迹中公开反馈的分数变化与公开候选估计，不接收场景真值。当前抽样为一阶分数残差近似，尚未提供已验证的完整天气和事件过程；效果提升也不能单独使搜索通过采用门槛。
- 第 6 层：按验证集冻结结构/权重后才生成独立正式和压力场景；有有效候选才增加 202/303 两个初始化。导出真实入口和模型，检查包大小，以及本机小场景和正式完整赛程入口。用户后续明确要求只在本机运行，本轮不再进行容器验收。

每场使用原始经校验的官方引擎自然结束。奖励和严格等于最终结算；后续回报不打折。按 64 次选择分段更新梯度时，保留完整历史状态和整场后续回报。训练标签始终来自官方引擎。

公开反馈、候选和实际动作逐步写入 `trace.jsonl.gz`；完整数字特征逐步写入 SQLite，训练按顺序读取，没有额外候选截断。每场成功后保存 `episode.json`，同时记录分项、时间、内存和模型身份。失败单独保存 `error.json`。恢复时复用已完整结束的场景；中断场景从头运行。模型、优化器和随机状态保存在 `resume.pt`。

默认结果目录是 `artifacts/global-planning-v2`：`state.json` 表示当前进度；各层目录保存训练和比较；`frozen.json` 表示独立测试前选定版本；`stage6/result.json` 表示最终结果。还没有最终结果文件时，不能把实现完成理解为训练、验证或采用已经完成。

第 2–5 层的研究吞吐比较不启用比赛计时；实际入口另由官方运行器检查。搜索目前的完整赛程比较也不能代替同等比赛 CPU 预算下的正式验收。

相同场景、相同模型参数和输入处理、相同动作代码的完整确定性结果可复用；检查点中的优化器或进度变化不触发相同推理的重复计算。报告用 `reused_from` 指向原执行记录，原耗时不会被缓存读取耗时替代。

本轮小场景第 3/4 层相对保留的第 2 层均未达到扩大门槛；第 5 层未来抽样也未验证。因此这些层的对照结果保留，本轮完整赛程先验证第 2 层。完整赛程结果另存，不能把小场景数字当作正式卡效果。
