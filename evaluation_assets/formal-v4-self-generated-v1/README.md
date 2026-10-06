# 正式 A–D 的自建本地评测卡

这些目录只供本地开发和跑分使用。每一张卡都复用对应正式卡下载包中的公开目标、天区、日历、台址、光纤和计分参数；天气、事件、预报、简报与限时请求均为**自行生成**，不代表也不能推断正式比赛或隐藏卡的真值。

| 卡 | 本地侧重点 | 用来检查什么 | 覆盖的自建事件 |
|---|---|---|---|
| A | 基础天气与公告 | 先检验是否会根据公开的天气公告调整常规观测。 | rainy, cloudy, terrain_obstruction |
| B | 方向性天气与避让 | 检验是否会结合方向公告、永久遮挡和很小的光纤方格选择指向。 | cloudy, smoggy, rocket_launch, terrain_obstruction |
| C | 诊断：天气、地震与隐藏故障 | 检验是否能区分会公开的天气或地震影响，与只能从结果推断的仪器故障。 | rainy, cold_wave, tornado, earthquake, instrument_fault |
| D | Hard mode：恢复与长期稳定性 | 检验在长期巡天中处理多类天气、隐藏故障、数据丢失状态更正和固定指向偏差的能力。 | rainy, cloudy, smoggy, rocket_launch, earthquake, instrument_fault, terrain_obstruction, data_loss, pointing_offset |

D 启用了 Hard mode，因而额外包含 `data_loss` 和 `pointing_offset`。A–C 没有这两类事件。所有常规事件类型至少在一张卡中出现：雨、云、霾、寒潮、风暴、火箭发射、地震、地形遮挡和仪器故障。

## 运行

从仓库根目录运行，替换 agent 命令和工作目录：

```bash
python3 examples/_local/runner/run_local.py \
  --card evaluation_assets/formal-v4-self-generated-v1/cards/a \
  --agent "python3 agent.py" \
  --agent-cwd /path/to/your-agent
```

把 `a` 改为 `b`、`c` 或 `d` 可运行其它卡。`truth/` 中的文件只给本地裁判使用；不要让智能体读取它们。每个卡目录的 `self_generated_metadata.json` 和 `generator/` 记录了来源、固定种子、事件配置和请求配置，因而可复建。

## 重新生成

```bash
python3 scripts/build-formal-self-generated-evaluations.py \
  --out evaluation_assets/formal-v4-self-generated-v1
```

默认输出目录已存在时，脚本会停止，避免覆盖已有本地结果。使用另一个 `--out` 路径可以生成一份新的确定性副本。
