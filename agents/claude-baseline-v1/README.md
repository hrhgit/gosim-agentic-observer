# claude-baseline-v1 · 巡天智能体基线

GOSIM Agentic Observer（survey26，`participant-agent-protocol-v4`）的一版完整智能体，
由 Claude 根据题目说明和四张正式卡的公开数据独立分析、设计并实现，作为后续版本的对照基线。

- 纯规则、确定性、只用 Python 标准库，不调用大模型，不读任何文件，所有状态来自 stdin。
- 本目录是一个独立的 git 仓库，放在主项目的 `agents/claude-baseline-v1/` 下；
  评测时引用主项目里的本地裁判程序和自建评测卡，除此之外不依赖主项目的任何代码。

> **参赛资格提示。** 比赛规则第 3 节第 6 条要求：作品需在自然语言理解、数据解析、任务规划、行动决策、
> 工具调用、计划自适应等环节中，至少有两个环节采用大模型驱动的智能体技术，否则不参与评奖
> （主办方主要通过分析最终版本的代码来判定）。本基线不调用大模型，**不满足这一条**：
> 它只能作为纯规则对照，不能原样作为参赛作品提交。

## 结果

### 收到的原文日志

平台只保留 stderr 最后约 2 MiB。旧版 `SURVEY26_INPUT_V1` 原文日志超过上限后，
前期记录会丢失；旧日志仍可用 `tools/extract_received.py` 检查和提取。
新版使用 `SURVEY26_CAPTURE_V2`，选择需要的字段并每 64 条压缩一次，stdout 仍只输出比赛回复。
下载新版运行的 `agent.log` 后，可以提取采集记录：

```sh
python3 tools/extract_capture.py /path/to/agent.log /path/to/captured.jsonl
```

输出文件必须尚不存在。每条输出包含接收顺序、选取后的 `message` 和明确的省略说明；
工具检查顺序和校验值，不能把它当作逐字原文或完整状态回放。
保留每次的新公告、预测、请求、观测结果、故障报告、数据丢失通知、模拟时间和分数；
开局仅省略公开目标表的行数据，保留列名、行数与内容指纹。其余开局数据保留。
决策时省略重复的最新公告/预测、请求进度、结束时间和运行时间余额；
若最新公告/预测未在新消息中出现且与前次不同，仍保留一次。未知字段保留。
普通日志之外，预计每卡采集记录约 0.31–0.90 MiB（依据已收到的尾部样本和自建卡开局估算，
不是完整线上测量）。完整保留情况待新版线上运行验证；未发送的隐藏天气数值仍需推断。
正常结束或输入关闭会写出不足 64 条的最后一批；进程被强制停止可能丢失尚未写出的这一批。

2026-10-05 起，本地工具默认使用 `formal-v4-observed-timeline-v4`：公告、预测和请求恢复线上已收到的内容，隐藏天气参数仍为标注假设。此前 v3 的设计说明保留如下。
它根据已下载的三次正式比赛公开反馈重新设计事件：更多雨云、两夜的 8 选 6 请求、D 的 15% 数据丢失上限，
仍全部为自建天气，不是官方隐藏数据。另有 `lighter` 与 `heavier` 新种子场景检查未知强度。
设计依据见主项目 [事件分析](../../docs/formal-feedback-event-design.zh.md)。
下面的原 v2 和留出集成绩保留为历史结果；重跑旧数据须明确使用 `--eval-set formal-v4-self-generated-v2`。

正式记录（官方本地裁判，`tools/run_eval.py`），评测集 `formal-v4-self-generated-v2`（正式卡公开输入 + 自行生成的天气/事件/请求，每卡 6 条请求、3 个故障）：

| 卡 | 官方示例 python-pro（纯规则） | 本基线 v1.0 | 差值 |
|---|---:|---:|---:|
| A | 23,518.8 | 26,457.0 | +2,938 (+12.5%) |
| B | 38,299.6 | 46,187.4 | +7,888 (+20.6%) |
| C | 27,177.1 | 29,394.4 | +2,217 (+8.2%) |
| D | 32,322.8 | 33,293.0 | +970 (+3.0%) |
| **四卡平均** | **30,329.6** | **33,833.0** | **+3,503 (+11.6%)** |

v1.0 在 v2 上：12 个故障全部报对（每卡 +300，无付费误报），请求 23/24，必观测漏 2 个（亮度过低，一小时曝光也达不到门槛），
每卡只用 33–121 / 900 的 CPU 预算。留出集 `holdout-reference-rates-s1`（新种子、引擎参考配置的事件频率，未参与调参）：
A 23,719.6 / B 44,991.7 / C 29,442.2 / D 33,292.6，平均 32,861.5，同样 12/12 故障、无付费误报。
同一留出集上 python-pro（纯规则）为 A 19,138.1 / B 39,139.4 / C 26,878.5 / D 32,418.4，平均 29,393.6，本基线高 11.8%。

这些是自建天气下的本地成绩，只用于比较智能体，不是线上成绩的预测。明细见 `results/` 和 [docs/03-experiments.md](docs/03-experiments.md)。

## 目录

```
agent/                 可提交的智能体（observer.project.json 在根目录）
  agent.py             协议主循环、节奏控制
  core/                几何、目标表、规划器、环境估计、指向偏差估计、状态
  pack_agent.py        打包成提交用 ZIP
docs/
  01-problem-analysis.md   题目与数据分析
  02-design.md             设计
  03-experiments.md        实验过程与结果记录
  data/card-analysis.md    四张卡的逐项数据（tools/analyze_cards.py 生成）
tools/
  run_eval.py          用官方本地裁判跑四张卡，汇总成绩（记录用的成绩都来自它）
  quick_sim.py         进程内快速模拟，开发时用
  sweep.py             批量比较参数
  inspect_run.py       单次运行的诊断
  loss_breakdown.py    一次正式运行相对理论上限的失分拆解
  upper_bound.py       完美排程的乐观上界（线性规划，需要 scipy）
  analyze_cards.py     公开数据分析
tests/                 与引擎逐项对照的几何测试、指向偏差估计测试
results/               每次正式评测的成绩与日志（大文件不入库）
```

## 运行

环境：Python 3.12（智能体本身只用标准库；`tools/analyze_cards.py` 需要 numpy）。
以下命令都在本目录下执行，默认主项目位于 `../..`，也可以用环境变量 `OBSERVER_PROJECT_ROOT` 指定。

```bash
python3.12 tools/run_eval.py --tag my-run
```

```bash
python3.12 tools/quick_sim.py --card a --nights 20
```

```bash
python3.12 tests/test_geometry.py
```

```bash
python3.12 tests/test_pointing.py
```

```bash
python3.12 agent/pack_agent.py --out claude-baseline-v1.zip
```

## 评测数据说明

本地评测默认使用主项目的 `evaluation_assets/formal-v4-observed-timeline-v4/`。
原评测集 `evaluation_assets/formal-v4-self-generated-v2/`，以及用 `tools/make_holdout_set.py`
另行生成的留出集 `eval_sets/holdout-reference-rates-s1/`：正式卡 A–D 的公开输入原样不动，
天气、事件、预报、公告和限时请求由主项目的脚本用引擎自带的生成器**自行生成**，固定种子，可复现。
它们不是正式比赛的隐藏数据，本地成绩只用于比较不同智能体，不能当作线上成绩的预测。
没有使用、下载或恢复任何官方练习卡。智能体运行时不读取任何卡片文件；
`tools/inspect_run.py` 为了事后诊断会读取自建卡的真值，这部分信息不会进入智能体。
