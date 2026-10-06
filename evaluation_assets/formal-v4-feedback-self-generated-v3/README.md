# 正式卡 A–D：根据线上反馈重新设计的本地事件（v3）

正式公开目标、天区、仪器、计分和日历原样复制。天气、公告、事件、请求全部**自行生成**，不是官方隐藏数据。
设计依据是同一版本三次正式运行的参赛者结果，三个运行共享同一套公开事件，不能当作三套独立天气。
完整分析见仓库 `docs/formal-feedback-event-design.zh.md`。

| 卡 | 主评测事件数量（由公开连续公告段设计，不宣称是官方事件总数） |
|---|---|
| A | 寒潮 4、火箭 4、地震 2；故障 1（已修复数量下限） |
| B | 雨 40、云 48、寒潮 4、火箭 4、地震 2；故障 1 |
| C | 雨 35、寒潮 4、火箭 4、地震 2；故障 1 |
| D | 雨 18、云 30、霾 15、寒潮 12、风暴 3、火箭 12、地震 2；故障 3 |

全部卡每条请求为 8 选 6、门槛 0.5、奖励 100 分、两夜期限，共 6 条。D 数据丢失窗口上限为 15%。
主评测不加永久地形遮挡，因为全部初始公告均未出现；较重场景另外加入，用来检查迁移能力。
天气强度、关闭概率、精确方向范围、地震强度、漏检故障和指向偏差没有唯一答案，保留明确假设。

已生成：nominal, lighter, heavier。`nominal` 是主设计，`lighter` 和 `heavier` 使用独立种子检查不确定情况；
它们是敏感性检查，不是有统计保证的上下界。仍有未覆盖的未知情况，例如晚季才出现或聚集发生的隐藏故障。
所有配置都在 `profiles.json`，每卡 `generator/`、`self_generated_metadata.json` 和总 `manifest.json` 可追溯来源与哈希。

```bash
python3.12 examples/_local/runner/run_local.py \
  --card evaluation_assets/formal-v4-feedback-self-generated-v3/nominal/cards/a \
  --agent "python3.12 agent.py" --agent-cwd agents/claude-baseline-v1/agent \
  --out artifacts/formal-feedback-2026-10-05/local-v3/a --quiet
```

将卡字母与场景目录替换可运行其余卡。`truth/` 仅由本地裁判使用，智能体通过标准输入接收按时公开的消息。
目前文件夹约定不能阻止带文件工具的智能体偷读答案；本次基线本身不读任何卡片文件。
旧 v1/v2 及留出集不替换，新场景在独立目录运行；本地分数不能作为线上分数预测。

```bash
python3.12 scripts/build-feedback-local-evaluations.py \
  --feedback artifacts/formal-feedback-2026-10-05/feedback-analysis.json \
  --out evaluation_assets/formal-v4-feedback-self-generated-v3-copy
```

输出已存在时停止，避免覆盖。固定种子可复建，官方时刻和请求目标没有复制到新事件。
