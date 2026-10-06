## 1. 概述

平台评测面向 DESI 式巡天的观测智能体。一个场景由三个文件组成：`weather.csv`（900 秒时隙，含视宁度、透过率、天光亮度与开/关标志）、`tiles.csv`（指向目录，含项目、优先级、曝光时间与目标计数）和 `score_config.json`（冻结常数）。智能体把场景变成 `decisions.csv`，`scorer.py` 把 `decisions.csv` 变成评分报告。

获得分数有两条路径：

1. 自己在公开场景上运行智能体，上传 `decisions.csv`。
2. 上传智能体；平台通过 observer-v1 逐步协议在隐藏场景上运行它，并对其产出评分。

两条路径使用同一个 `scorer.py`。入门包包含平台使用的全部文件。

## 2. 入门包

从资源页下载 `agent-observer-starter-kit.zip`。内容：

| 文件 | 用途 |
|---|---|
| `agent.py` | 实现 observer-v1 协议的基线智能体。替换 `decide()` 即可。 |
| `local_runner.py` | 通过协议运行智能体并在本地评分。 |
| `protocol.py` | 构建智能体看到的状态。与平台副本完全相同。 |
| `scorer.py`、`score_config.json` | 冻结的评分器与常数。 |
| `generate_example_data.py` | 有种子的天气/天区场景生成器。 |
| `example/` | 已发布的开发场景（种子 11）：2 夜 × 12 时隙，72 个天区。 |
| `sac_submit.py` | 使用 API 令牌的命令行提交。 |
| `SKILL.md` | AI 编程助手可端到端照做的说明。 |

运行基线：

```
python3 local_runner.py --agent agent.py --weather example/weather.csv --tiles example/tiles.csv --config score_config.json --out run_output
```

最后一行打印得分。`run_output/decisions.csv` 即可作为结果文件上传；`run_output/score_report.json` 与平台生成的报告一致。

生成不同的场景以避免只针对一种天气序列调参：

```
python3 generate_example_data.py --seed 7 --n-nights 5 --slots-per-night 20 --n-tiles 150 --output-dir scenario7
```

## 3. 数据格式

约定：UTF-8，逗号分隔，必须有表头，允许未知的额外列。时间戳为 UTC 的 RFC 3339（`2026-10-02T02:00:00Z`）。布尔值接受 `true/false` 或 `1/0`。ID 为字符串，在各自表内唯一。规范拼写为 `transparency` 与 `program`。

### weather.csv

```
slot_id,night_id,timestamp_utc,duration_seconds,seeing_arcsec,transparency,sky_brightness,is_observable
```

行按时间顺序。同一夜内的时隙连续，长度为 `slot_seconds`（900）。只允许在不同 `night_id` 之间有间隔。`is_observable=false` 表示穹顶关闭：此时的曝光得分为零并计为浪费。

### tiles.csv

```
tile_id,ra_deg,dec_deg,program,region,priority,nominal_exptime_seconds,n_lrg,n_elg,n_qso,n_bgs
```

`program` 为 `DARK`、`BRIGHT` 或 `BACKUP`。`region = floor(ra_deg / 45)`（0–7）是用于完成度与均匀性报告的分区。`priority` 在 [0, 10]。`nominal_exptime_seconds` 是一次完成该天区所需的不间断时间。

### decisions.csv

```
decision_id,slot_id,action,tile_id,program,reason
```

1. 按文件顺序评估。`decision_id` 唯一且严格递增。
2. `slot_id` 是动作开始的时隙。多行可用同一时隙，由 `decision_id` 排序。时隙顺序不能倒退。
3. `observe` 使用天区完整的 `nominal_exptime_seconds`，可延续到同一夜的后续时隙。若在某时隙内提前结束，另一条使用相同 `slot_id` 的决策可使用剩余时间。没有决策使用的时间为空闲。
4. `wait` 消耗当前时隙的剩余时间。`wait` 的 `tile_id` 与 `program` 必须为空；`observe` 两者必填，且 `program` 必须等于天区的项目。
5. 无法在该夜结束前完成的曝光无效：零分，至夜晚结束的时间计为浪费。已完成的天区不能再次观测。
6. 格式错误的表被判为无效提交。操作层面无效的动作会列在报告中，消耗时间但不得分。

### score_report.json

顶层字段：`score`、`science_score`、`waste_penalty`、`total_waste_seconds`、`waste_breakdown_seconds`（idle、invalid_actions、unproductive_exposure）、`unavailable_unpenalized_seconds`、`completed_tiles`、`invalid_actions`、`region_completion`，以及每条决策一项的 `actions`（含 `valid`、`message`、`start_timestamp_utc`、`elapsed_seconds`、`segments`、`unproductive_seconds`、`science_score`）。

## 4. 观测员协议（observer-v1）

平台以 `python3 -I -B agent.py` 启动入口脚本，通过标准输入/输出通信，每行一个 JSON 对象。标准错误被记录为日志，可在提交页下载。标准输出不要打印其他内容。

### 你会收到的消息

1. `{"type": "init", "protocol": "observer-v1", "config": {...}, "site": {...}, "slot_seconds": 900, "n_tiles": N, "tiles": [...], "scenario": {...}, "limits": {...}}` 一次。`tiles` 是完整目录（`tile_id, ra_deg, dec_deg, program, region, priority, nominal_exptime_seconds, n_lrg, n_elg, n_qso, n_bgs`）。
2. `{"type": "step", ...}` 每个决策点一次，包含：
   - `now`：`slot_id`、`night_id`、`slot_index`、`timestamp_utc`、`slot_elapsed_seconds`、`slot_remaining_seconds`、`night_remaining_seconds`、`slots_remaining_in_night`、`slots_remaining_total`。
   - `weather`：`seeing_arcsec`、`transparency`、`sky_brightness`、`is_observable`、`zenith_quality`（大气质量为 1 时的 A）、`zenith_program`。
   - `forecast`：未来四个时隙（`slot_id`、`night_id`、`timestamp_utc`、`seeing_arcsec`、`transparency`、`sky_brightness`、`is_observable`）。
   - `progress`：`completed_tiles`、`total_tiles`、`science_score`、`waste_seconds`、`idle_seconds`、`invalid_seconds`、`unproductive_exposure_seconds`、`invalid_actions`、`region_completion`、`completed_tile_ids`。
   - `available_tiles`：未完成、能在剩余夜晚内完成、且在首个曝光段中点高度高于 30° 的天区。每项含目录字段以及 `altitude_deg`、`airmass`、`quality`（该天区此刻的 A）、`condition_program`、`program_match`、`target_value`、`expected_gain`（假设整个曝光都在当前时隙天气与当前大气质量下的得分）与 `expected_gain_per_second`。按 `expected_gain` 降序排列。
   - `last_action`：上一次回答的结果（`valid`、`message`、`science_score`、`elapsed_seconds`），或 `null`。
3. `{"type": "end", "summary": {...}}` 最后一个时隙之后一次。此后可以退出。

### 你要发送的消息

每个 `step` 恰好一行：

```
{"action": "observe", "tile_id": "200069", "program": "DARK", "reason": "highest expected gain"}
{"action": "wait", "reason": "dome closed"}
```

`plan` 可作为 `program` 的别名。`observe` 省略 `program` 时使用天区自身的项目。`reason` 为不超过 500 字符的自由文本，会写入 `decisions.csv`。

你可以选择不在 `available_tiles` 中的天区；评分器会按正常规则处理（低于高度限制的曝光段无效，跨夜为无效动作）。`available_tiles` 是便利信息，不是限制。

### 时间与步数

每个回答都消耗时间：`observe` 消耗天区的曝光时间（无法完成时消耗该夜剩余时间）；`wait` 消耗当前时隙剩余时间。到达天气终点时运行结束。168 个时隙的场景最多需要几百步。

## 5. 平台运行限制

| 限制 | 值 |
|---|---|
| 解释器 | Python 3.12，`-I -B`，仅标准库 |
| 网络 | 无 |
| 每步决策 | 20 秒 |
| 每个场景 | 600 秒总时间 |
| 内存 | 1 GB |
| 写入文件 | 合计 64 MB，位于运行目录（`$OBSERVER_SCRATCH`） |
| 进程 | 64 |
| 程序包 | `.py` 或 `.zip`，≤ 20 MB，≤ 2,000 个文件，解压 ≤ 50 MB |

智能体可用的环境变量：`OBSERVER_PROTOCOL=observer-v1`、`OBSERVER_SCRATCH`（可写目录）、`HOME` 与 `TMPDIR`（同一目录）。

## 6. 提交

### 通过网站

控制台 → 提交。选择阶段、提交类型、场景（仅结果文件）与文件。页面显示队伍今日剩余次数。每次提交都有独立页面，展示得分、各场景报告、动作时间线、分区完成度、生成的 `decisions.csv` 与智能体日志。

### 通过命令行

API 令牌在个人资料页。

```
python3 sac_submit.py --base https://<host> --token <token> --phase practice --kind results --scenario dev-example --file run_output/decisions.csv
python3 sac_submit.py --base https://<host> --token <token> --phase online --kind agent --file agent.py --wait
```

`--wait` 会轮询直到评测结束并打印得分。

### JSON API

所有端点返回 JSON。使用 `Authorization: Bearer <token>` 认证。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/health` | 存活检查 |
| GET | `/api/phases` | 阶段、时间窗、场景 |
| GET | `/api/leaderboard?phase=<slug>&limit=<n>` | 排名（`entries`：rank、team_name、total_score、science_score、completion_rate、uniformity_score、submission_count） |
| GET | `/api/announcements` | 已发布公告 |
| GET | `/api/me` | 账号与队伍 |
| GET | `/api/submissions` | 本队提交 |
| POST | `/api/submissions` | multipart 表单：`phase`、`kind`、`scenario`（仅结果文件）、`file`，可选 `title`、`notes` |
| GET | `/api/submissions/<id>` | 状态、得分、各场景评测 |
| GET | `/api/submissions/<id>/evaluations/<eid>/{report,decisions,log}` | 产物 |

交互式文档：`/api/docs`。

## 7. 策略提示

1. `expected_gain` 假设整个曝光都处于当前时隙的天气。夜晚后段的长曝光可能进入更亮的天光，请查看 `forecast`。
2. `program_match` 很重要：加成 DARK 25%、BRIGHT 15%、BACKUP 5%，不匹配没有额外加成。在 BRIGHT 天况下观测 DARK 天区仍按 A 得分，只是没有加成。
3. 等待每秒扣 0.02（一个空时隙 18 分）。观测低价值天区常常好于等待，除非预报中很快会有明显更好的天区可见。
4. 会跨夜的曝光无效并浪费该夜剩余时间。`available_tiles` 已排除这类天区。
5. 长曝光期间高度会变化。正在升起的 31° 天区比正在落下的 31° 天区更安全。

## 8. 本地核对清单

1. `python3 local_runner.py ...` 无警告结束并打印得分。
2. 用 `python3 scorer.py --weather ... --tiles ... --decisions run_output/decisions.csv --config score_config.json` 重新评分得到相同数字。
3. 智能体不使用第三方库，不读取自身目录之外的文件。
4. 每步决策不超过几秒。
