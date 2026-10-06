# typescript-pro —— 用 TypeScript 实现的 python-pro 策略

[English README see README.md](README.md)

这是 [`../python-pro`](../python-pro/README.zh.md) 的 TypeScript（Node.js 22+）移植版。python-pro 是 GOSIM survey26 望远镜巡天赛题（`participant-agent-protocol-v4`）的高分参考智能体。两者策略、常数和大模型环节都一样，在本地卡上做出的**决策也完全相同**：固定搜索力度并使用确定性的模型桩时，它的 `decisions.csv` 与 python-pro 的逐字节一致。运行时只用 Node 内置功能（`fetch`、`zlib`、`readline`）；`typescript` 和 `@types/node` 只在编译时需要。和 python-pro 一样，它只用协议在运行时提供的信息（星表、公开评分配置、公报、预报、自己的观测结果），从不读取卡片文件。

完整的设计说明（每一部分为什么这样做、还能在哪里超过它）请看 **[python-pro 的 README](../python-pro/README.zh.md)**。本页只给简要版本，以及 TypeScript 特有的部分。

## 成绩（本地引擎，`run_local.py`）

在四张本地卡 L1-L4 和选手套件的 demo 卡上与 python-pro 做了两组对比，模型用本地替身（一个按规则回答三个模型环节的 OpenAI 兼容桩）：

| 卡 | 固定力度（`PRO_FIXED_LEVEL=0`）python-pro | 固定力度 **typescript-pro** | 自适应节奏 + 慢模型 python-pro（均值 ± 标准差，3 次） | 自适应节奏 + 慢模型 **typescript-pro** |
|---|---:|---:|---:|---:|
| L1 | 6,121.4 | 6,121.4 | 6,211 ± 65 | 6,123 ± 2 |
| L2 | 6,392.7 | 6,392.7 | 6,353 ± 115 | 6,387 ± 62 |
| L3 | 6,796.4 | 6,796.4 | 6,676 ± 143 | 6,781 ± 21 |
| L4 | 6,789.3 | 6,789.3 | 6,668 ± 121 | 6,773 ± 28 |
| demo | 1,717.8 | 1,717.8 | 1,717.8 ± 0 | 1,717.8 ± 0 |

- **固定力度**（搜索力度固定、桩立即回答）：两个智能体都是确定性的，五张卡上的 `decisions.csv` 逐字节相同（各跑两次）。
- **自适应节奏 + 慢模型**（正常时钟；30% 的模型回答要 20–40 秒，部分会晚到）：更接近真实情况，有运行间波动。typescript-pro 每次决策约 25 毫秒 CPU，python-pro 约 160 毫秒，所以在繁忙的机器上 python-pro 有时会降到更便宜的搜索力度，typescript-pro 从不降级；python-pro 较大的波动主要来自这里。
- **平台**（隐藏测试队伍，练习卡，2026-10-04）：α 7,129、β 6,885、γ 6,769、δ 6,504（平均 6,822），每张卡都在公平时钟内 `survey_complete`。那次运行时队伍的模型 key 超出了额度，所有模型调用都失败、由规则决定（这是设计好的回退）。

## 目录结构（与 python-pro 一一对应，方便对照阅读）

```
src/agent.ts       agent.py        入口：协议主循环、节奏控制、仪器故障报告、大模型环节的接线
src/planner.ts     planner.py      一次搜索同时决定指向、光纤、曝光时长和项目；从观测结果中学习
src/skymath.ts     skymath.py      公开天球几何：恒星时、地平坐标、切平面投影、光纤网格、月亮
src/advisor.ts     advisor.py      大模型环节：夜间计划、故障复核、付费报告确认
src/llmClient.ts   llm_client.py   OpenAI 兼容客户端（内置 fetch），调用在后台进行
src/packAgent.ts   pack_agent.py   打包上传用的 ZIP（不会打包 .env）
observer.project.json   平台清单：镜像 node:22-slim，构建 `npm ci --include=dev` + `npm run build`，运行 `node dist/agent.js`
.env.example            复制成 .env，本地运行前填好 API key
```

## 设计简述

- **每次决策一次搜索**，同时决定指向、光纤分配、曝光时长和项目，目标是 `收益 − λ·T`。候选视场：最值钱的 12 个目标分别放到 16 根光纤上，再加上剩余科学价值最密的 20 块天区，最后用小步平移微调。
- **必选目标和观测请求**按成功概率加奖励；暗弱的必选目标等到接近它最好的天空条件、且不是预报坏天气的夜晚再观测。
- **用饱和命中判断项目档位**：饱和命中能精确看出项目倍率，档位水平按这些命中拟合。
- **仪器故障**：`E = 质量水平 / 档位水平`（天气两者都压低，故障只压低质量）。先用免费误报额度，付费报告需要持续的证据。地震公告出现后 12 小时内不探测；地震影响可能还在的期间，只有 E 又下了一个新台阶才探测。
- **隐藏的指向偏差（Hard mode 卡）**：在按光纤间距缩放的网格上给候选偏差打分（最优点落在边缘时自动扩大范围），依据是哪些目标命中、哪些落空。
- **公平时钟**：智能体用 `process.cpuUsage()` 测自己每次决策的 CPU 时间，与 `wallclock.remaining_real_cpu_seconds` 按剩余决策数摊开后的预算比较，同时留意实际时间上限（`wall_remaining_seconds`），需要时降低搜索力度。
- **大模型：每晚两个环节，付费报告前再加一次**：夜间计划（预报 + 公报 → `bad_night`、要避开的方位）、故障复核（自己的逐小时质量表 → `fault_likely`，决定付费报告的门槛），以及可以否决付费报告的确认。调用在后台进行，从不阻塞决策：每晚开始时只在剩余实际时间允许的范围内等待，晚到的答案到了再用，失败时保留规则的默认值。HTTP 429/5xx、超时和网络错误会退避重试（遵守 `Retry-After`）。

## TypeScript 相关说明

- **数值与 Python 一致。** `src/skymath.ts` 复现了 Python 浮点的 `%`、`//`、`round()` 和 `sum()`（Python 3.12 起是补偿求和），规划器也保持了浮点运算的顺序。迭代顺序会影响结果的容器都用 `Map`，因为普通对象会把整数样式的键重新排序。所以两个智能体能逐个决策对上。
- **用异步代替线程。** python-pro 在后台线程里调用模型；这里用 promise。它们在智能体等待下一行 stdin 时推进，所以规划器从不等网络。stdin 关闭后进程立即退出，即使还有调用没回来。
- **同样的决策，CPU 时间约为 python-pro 的 1/6。** 在公平时钟下，这留出了加大搜索的空间，可以试试 `PRO_POOL`、`PRO_N_ANCHORS`、`PRO_N_DENSE` 等。

- **平台构建。** 平台构建时使用清单里的环境变量，且 home 目录只读，所以清单设置了 `NPM_CONFIG_CACHE=/workspace/.npm-cache`（把 npm 缓存放在项目目录里），并用 `npm ci --include=dev` 安装（TypeScript 编译器是开发依赖）。清单里的环境变量名必须大写。

## 配置（.env）

```
OPENAI_API_KEY=sk-...                            # 必填（也认 KIMI_API_KEY）
OPENAI_BASE_URL=https://api.kimi.com/coding/v1   # 默认；中国大陆以外账号用 https://api.kimi.ai/coding/v1
OPENAI_MODEL=k3                                  # 默认
```

没有 key 时，程序启动即退出并提示 `missing API key: set OPENAI_API_KEY`；`OBSERVER_MODEL_DISABLED=1`（选择「本次不提供模型」或 `survey26 eval start --no-model` 的评测由平台设置）时例外：此时不需要 key，智能体只用规则运行。在平台上，`OPENAI_BASE_URL` / `OPENAI_API_KEY` 会自动注入。`k3` 只接受默认温度，所以客户端不发送 temperature。

## 本地运行

```bash
npm ci && npm run build
python3 ../_local/runner/run_local.py --inherit-env --card ../_local/cards/L1 --agent "node dist/agent.js" --agent-cwd .
npm run pack                                     # -> ../typescript-pro-agent.zip
```

`src/planner.ts` 和 `src/agent.ts` 顶部的每个常数都可以用 `PRO_<名字>` 环境变量覆盖（例如 `PRO_LAMBDA_FRAC=0.5`），与 python-pro 相同。`PRO_FIXED_LEVEL=0` 固定搜索力度，在繁忙的机器上做对比更可复现（平台上用的是自适应节奏）。

## 许可

任务卡、模拟数据、评测代码和示例项目采用 [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) 许可；请引用 GOSIM 2026 Agentic Observer Hackathon（https://create.gosim.org/survey26/）。详见 `../LICENSE.md`。
