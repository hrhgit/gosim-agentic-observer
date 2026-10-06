# 异常事件与上报功能线：修改记录（中文）

日期：2026-09-18（2026-09-20 复核：订正文件/测试计数，并标注已被后续重构移除的中间态）
范围：`agent-observer/` 仓库，89 个已跟踪文件修改 + 11 个未跟踪条目
状态：全部改动在工作区**未提交**；本地测试全绿（124 passed, 1 skipped）

本文档汇总"异常事件与 agent 上报"功能线（计划图节点 MP-045 至 MP-052）对
本仓库的修改。该线之后还有若干轮后续迭代（故障作用域限定 REGION_SET、故障
持续语义、severity/效率乘数配置化、快照隐藏 `instrument_efficiency`、program
档位判定去效率、report 并入 decisions.csv）；下面只保留最终结论与必要的限定语，
不再逐轮展开。
各节点的完整设计决策、验收证据见 `docs/plan/` 下的 MP 节点副本
（从工作区 `_plan/plan/` 复制；其中 artifacts 路径相对 `_plan/` 解读）。

## 功能概述

在 challenge v3 基础上新增几大能力：

1. **Agent 可见最近一次完成观测的真实得分**：
	- agent 可用 `scoring_preview.py` 对当前快照的合法候选做公开基线估值；
	- 每个决策点的快照携带 `tile_last_finished`：最近一次完成观测的真实得分；
	- 估值与真实值的差异来自快照看不到的隐藏因素：仪器故障的效率乘数、nova/reddening 标签乘数，以及曝光期内的天气变化——agent 据此检测异常。
2. **仪器故障事件**：日常的`instrument_efficiency` 会在 [0.90, 1.00]范围内抖动（在`challenge/reference/config/weather_config.json`的`quality`中调节；该字段不进入 agent 快照，preview 基线不乘效率）；
	- 新增的`instrument_fault` 事件会把某区域的仪器效率降低到 [0.4, 0.7]（在`challenge/reference/config/weather_config.json`的`instrument_fault`-`instrument_efficiency_multiplier_range`中配置），同一时刻至多存在一个故障。
	- 事件不会在weather中对 agent 进行公开播报（`include_instrument_faults=False`）。
	- agent通过得分异常判断仪器是否故障；发现故障后通过新增的`report`动作进行上报。`report`动作不占用slot。
	- 正确上报后1天后，在每晚更新的天气信息里公布受影响的天区；2天后恢复正常（时间通过`challenge/reference/config/score_config.json`的`fault_response`进行调节）。故障不自行结束：修复是唯一提前结束途径，否则持续到巡天末。
	- 每两次正确故障上报之间允许1次错误上报，超过1次后扣分。正确上报后刷新此计数。次数通过`challenge/reference/config/score_config.json`中`reporting`的`fault_misreport_free_allowance`调节.
	- 故障正确上报的回报是故障信息公布与故障修复（无直接加分）；故障误报超额每次 −100（通过`challenge/reference/config/score_config.json`中`reporting`的`fault_misreport_penalty`调节）。
3. **新增两类特殊tile属性--nova和reddening**：生成`tile_anomalies.csv` ，给随机 tile 打上 nova/reddening标签，静默作用于评分；nova tile的实际得分会变为preview的1.5倍；reddening tile的实际得分会变为preview的0.8；agent需要找出nova和reddening tile，通过`report`操作上报。奖励分在计算总分时加入（通过`challenge/reference/config/score_config.json`中`reporting`的`"reward_correct": 100.0, "penalty_wrong": 150.0`调节）。
4. **重复观测**：任意 tile 可重复观测，科学分取历次最高（max-score）；完成(`complete`)语义仍以首次合法观测入账；request 访问计数与得分彻底解耦。
5. **上报通道**：
	- `decision_response` 可携带 `reports` 数组（`Instrument_Failure` / `NOVA {tile}` / `Reddening {tile}`），不占 slot；
	- 快照新增 `tile_last_finished`（实现分反馈）与 `fault_status`（正确上报 x1=1 天后发布、维修期每晚重发、x2=2 天修复后移除；
	-  report 行直接写入 decisions.csv（`report_instrument_failure`/`report_nova`/`report_reddening` 三种 action），单文件 sha256 审计链。

参考/minimal agent 新增确定性检测层（`anomaly_detection.py`），无 API key
即可运行：读数占比规则确认标签、故障崩塌窗口触发上报、维修期避让故障区域、
残局做确认性重复观测。dev-reference 基线：4 标签全对、故障 1/1 检出、0 误报。

> 设计说明：minimal agent / reference agent 只是**设计示例**，展示接口用法与
> 检测思路；其检测参数（`SAC_ANOMALY_*` 阈值）不求精确，只要运行顺畅、不产生
> 非法动作即可。参数校准不是本平台代码的目标。

## 版本与锚点（最终态）

| 项 | 最终值 |
|---|---|
| 参与者协议 | `participant-agent-protocol-v2` |
| 决策快照 | `decision-snapshot-v3` |
| 天气 schema | `directional-weather-v2` |
| 基线锚点 | `23430.568406` |

基线锚点 = `scenarios/dev-reference` 上确定性 minimal agent 的 `total`
（`termination_reason = survey_complete`）：`tests/test_starter_kit.py` 与工作区根
`AGENTS.md` 写入精确值 `23430.568406`，`archive/starter_kit_v3/README.md` 与
`archive/starter_kit_v3/SKILL.md` 写作"约 23430.57"，四处一致。

## 分节点修改记录

> 说明：MP-049/MP-050 记录的是该功能线的中间态。这两个节点之后，report 通道又
> 经历一次重构：取消独立 `report.csv`，改为把接受的上报展平为 `decisions.csv`
> 中紧随承载决策的 `report_*` 动作行（单文件 sha256 审计链）。凡下文出现
> `REPORT_COLUMNS`、`load_reports`、`apply_decision_stream`、
> `score_decisions.py --reports`、`report.csv` 的条目，均已被该重构移除；
> 最终形态一律以 `decisions.csv` 承载。

### MP-046 契约冻结

- `challenge/contracts.py`：协议/快照版本 bump；新增 `REPORT_KINDS`、
  `ANOMALY_TAG_VALUES`、`TILE_ANOMALY_COLUMNS`（同批的 `REPORT_COLUMNS`
  已随 report 并入 decisions.csv 移除）。
- `challenge/participant_agent/protocol.py`：本地版本常量同步。
- 三个场景 `score_config.json`：删 `one_ordinary_credit_per_tile`，新增
  `repeat_observation` / `reporting` / `anomaly_tags` / `fault_response` 占位小节；
  manifest sha256 刷新。
- 测试断言（test_participant_protocol.py）与 `scoring_preview.py` 版本钉更新。

### MP-047 效率抖动与仪器故障

- `challenge/weather_simulator.py`：schema v2；`CONDITIONS` 增加
  `instrument_fault`；逐 slot 抖动；故障确定性不重叠放置；预报排除故障
  （含假阳性）；`get_effective_conditions(..., include_instrument_faults=...)`。
- `challenge/challenge_workflow.py`：快照两处调用传 False（隐藏故障）。
- 三个 `weather_config.json`：jitter 区间 + instrument_fault 事件定义
  （后于简化步骤改为 `REGION_SET` 1.0）。
- 场景重生（仅 weather 三件套/metadata/manifest 变化）；fixture 重评分。
- 新增 5 个 weather 测试（该线后续轮次另有追加，最终
  `challenge/tests/test_weather_simulator.py` 15 例）。

### MP-048 最高分重复观测

- `challenge/scoring_core.py`：删 duplicate_tile 判非法；逐 tile 最高分账本
  （只入账正向增量）；request 重访正常计分；visit 与得分解耦；
  avoidable-wait 与"重复观测能否改善"精确挂钩（真相天气前向模拟）。
- `challenge/scoring_preview.py`：已完成 tile 给边际估值（可选
  `tile_best_scores` 参数，缺省保守为 0）。
- 7 个新评分测试 + 2 个 preview 测试（后者在
  `challenge/tests/test_minimal_agent.py`；该线后续轮次另有追加，最终
  `challenge/tests/test_scoring_core.py` 16 例）；baseline 测试 wallclock 120s→240s
  （本机 aarch64 环境 flake，HEAD 对照证明非回归）。

### MP-049 隐藏标签与结算

- `challenge/scenario_builder.py`：`tile_anomalies.csv` 生成（seed+4000 独立
  rng 流，不进选手默认下载清单）；manifest/describe 条件性纳入。
- `challenge/tile_config.json`：`anomaly_tags` 数量配置（nova 2 / reddening 2）。
- `challenge/scoring_core.py`：`load_tile_anomalies`；分段乘数（base 与 bonus
  同缩放；quality band 用未乘值）；`Report`/`apply_report`
  故障账本与修复截断（scorer 侧 end_overrides，不改真相文件）；finalize
  tag 结算。（同批的 `load_reports` 与 `apply_decision_stream` 双文件归并
  已随 report 并入 decisions.csv 移除。）
- `challenge/score_decisions.py` 与 `archive/starter_kit_v3/score_decisions.py`：曾新增
  `--reports`（已随 report 并入 decisions.csv 移除，复放回归单文件）。
- 新增 test_report_settlement.py（当前 11 例）。

### MP-050 协议通道与反馈

- `challenge/challenge_workflow.py`：快照 `tile_last_finished` / 时门
  `fault_status`（fault/normal 两种形态，夜初发布）；reports 校验
  （畸形条目单独丢弃、合法 action 保留、重复容忍）；当时恒写 report.csv
  （已移除：接受的上报改为展平进 decisions.csv）。
- `challenge/participant_agent/protocol.py`：`decision_response(..., reports=None)`。
- `archive/starter_kit_v3/local_runner.py`：当时额外产出 report.csv 并纳入复放
  （已移除，复放只看 decisions.csv）。
- `worker/main.py`：当时上传 report.csv 到 results 并纳入结算
  （已移除，scorer 直接读 decisions.csv 里的 `report_*` 行）。
- 新增 test_report_channel.py（10 例）。

### MP-051 参考 agent 异常检测

- 新增 `challenge/participant_agent/anomaly_detection.py`（镜像
  `archive/starter_kit_v3/agent/`）：偏差比值检测、`SAC_ANOMALY_*` 可调阈值、
  占比确认规则、故障证据窗口、fault_status 状态机、维修期避让、
  嫌疑确认观测；`decision_graph.py`/`minimal_agent.py`/`state.py` 接入；
  `reference_strategy.py` 教学段。
- `challenge/run_challenge.py` ReferenceAgent：残局重复观测不再 wait 受罚。
- 新增 test_anomaly_detection.py（12 例）。

### MP-052 发布验证与文档

- 参赛者文档：starter_kit 的 README/SKILL/QUICKSTART（中英）全面同步
  新协议、新语义、新锚点。
- 组织者文档：docs/competition-format.md（异常发布专节、校准旋钮清单、
  开放事项）、docs/organizer-guide.md（4.5 校准节、种子轮换覆盖新 artifact）。
- web 公开站：docs/rules/start 中英双语、i18n、ProtocolExplorer.vue、
  protocol-sample.json、lib/report.ts 类型；顺带修复 rules.en.md 一处
  预存段落错位。
- 三处版本断言解锁（live_e2e、e2e_web、test_starter_kit 的协议断言）。

### 其他

- `.gitignore`：新增 `run_output/`（本地试跑产物目录，本会话早期加入）。

## 新增文件清单

- `challenge/participant_agent/anomaly_detection.py`（+ starter_kit 镜像）
- `challenge/tests/test_report_settlement.py`、`test_report_channel.py`、
  `test_anomaly_detection.py`
- 三个场景的 `outputs/reference/tile_anomalies.csv`
- `challenge/reference/outputs/workflow_reference/`（ReferenceAgent 重跑产物，untracked）
- `docs/plan/MP-045..MP-052`（本记录对应的计划节点副本）

## 验证状态

- `pytest challenge/tests tests/test_starter_kit.py tests/test_challenge_runner.py`
  → 124 passed, 1 skipped（playwright 渲染用例缺装，一贯 skip）。
- 全量 `pytest challenge/tests tests/ --ignore=tests/supabase
  --ignore=tests/e2e_web` → 退出码 0。
- web 生产构建（vue-tsc + vite + kit 打包）通过。
- dev-reference 选手路径冒烟：survey_complete，4 标签全对 +400，
  故障 1/1 检出上报，0 误报，decisions.csv 含 report 行、回放正常。
- demo-week（7 夜）同一路径冒烟：survey_complete，但检测只部分正确——
  2 个标签正确 + 1 个错报（`report_reward` 200，含 −150），故障 1/1。
  这是预期行为：demo 场景太短，参考检测器只是演示、不是已校准方案
  （QUICKSTART/SKILL 已注明）。
- 字节锁定、确定性生成、decisions.csv 单文件复放一致性测试全绿。

## 遗留问题（移交主办方）

1. 线上 hosted 全流程验证未做（需真实平台）：上传会上报的 agent 包，
   核对 decisions.csv（含 report 行）产物与审计链。
2. tests/supabase（需本地 Postgres+PostgREST）与 tests/e2e_web（需
   Playwright 浏览器）本机未跑；live_e2e 的 v2 断言需部署后验证。
3. 参考 agent 对持续故障的检出偏慢：dev-reference 的故障 12-10 起病、
   次年 1-20 才上报——故障区 tile 的最高分早已入账，残局重复观测优先级低，
   塌缩证据在滚动窗口里积累缓慢。净分为零靠"故障前已入账 +
   修复后补齐"，不保证对所有种子成立。更严重的情形已在合成场景实证：若故障区
   tile 在起病前全部高分入账，参考 agent 残局几乎不再观测该区域，
   持续故障可能长期甚至永不检出——主办方校准时应注意故障起病时刻与
   完成进度的关系，或考虑给参考 agent 加巡逻行为（检测层增强，未实现）。
   注：按设计 owner 决定，参考 agent 只是设计示例，其检测参数不求精确，
   本条不影响发布。
4. 正式赛前必须按 organizer-guide 轮换种子（覆盖天气、故障与标签）。

## 平台侧收编说明（2026-09-20）

本功能线并入平台主仓库时按"**双轨门控**"收编（合入提交见 git 历史）：

- **门 = 场景的 `score_config.json`**：含 `repeat_observation` / `reporting` / `anomaly_tags` / `fault_response`
  任一小节即启用全部新机制；一个都没有则整条链路（生成、快照、协议、判罚、计分）保持赛初合约逐字节不变。
- **练习场景全部走旧轨**：快照 `decision-snapshot-v2`、信封 v1、重复观测仍判 `duplicate_tile`、上报行拒收；
  旧黄金基线 12287.478365 在新代码下原样复现（tests/test_starter_kit.py）。
- **双向版本宽容**：平台接受 v1/v2 两代响应信封；入门包协议层接受 v2/v3 两代快照与 v1/v2 信封，
  一个程序包两个阶段通用。示例智能体在旧快照下自动关闭检测器与重复观测分支。
- **入门包捆绑练习场景不重生成**（与平台存储逐字节一致，用固定摘要锁死）；新增 `scenarios/finals-preview`
  （seed 20260920，7 晚，异常机制全开，附 `tile_anomalies.csv` 供本地复算）作为正式赛演练场，
  基线锚点 **8214.257133**，示例智能体在该场景 1/1 检出并正确上报仪器故障、0 误报。
- 原文写入的 dev-reference 锚点 23430.568406 属于"重生成后的 dev-reference"，收编后不适用；
  正式赛机制的公开锚点以 finals-preview 为准。
