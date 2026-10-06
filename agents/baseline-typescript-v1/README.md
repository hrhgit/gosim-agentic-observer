# 当前基线的 TypeScript 迁移版

从 `../claude-baseline-v1/agent` 迁入的独立项目，使用 **Node.js 22**，并通过常驻 Python 进程调用 **10 月 5 日 11:44 可信度优化版**观测条件模型和**仪器故障申报模型**。比赛构建阶段自动安装所需 Python 运行环境。

## 迁移范围

| Python 来源 | TypeScript 文件 | 保留的行为 |
| --- | --- | --- |
| `agent.py` | `src/agent.ts` | 输入输出、20 个默认参数、运行节奏、曝光反馈、完整输入日志 |
| `core/astro.py` | `src/core/astro.ts` | 天空位置、月亮影响和可观测范围 |
| `core/catalog.py` | `src/core/catalog.ts` | 目标分区、附近目标、季节可观测时长 |
| `core/conditions.py` | `src/core/conditions.ts` | 天气和仪器状态、地形学习、故障报告判断 |
| `core/planner.py` | `src/core/planner.ts` | 分区搜索、指向细调、曝光时长、每根光纤的目标选择 |
| `core/pointing.py` | `src/core/pointing.ts` | 指向偏差、反馈证据和不确定范围 |
| `core/state.py` | `src/core/state.ts` | 目标得分、请求截止时间、均匀度奖励、撤销曝光账本 |

`compat.ts` 保留 Python 的取余、向下整除、偶数样本的中位数和浮点求和处理。`Map` 保留插入顺序；需要按多个值排序的地方保留 Python 元组的比较顺序。候选分区较少时保持原来的遍历顺序。

搜索和参数取自当前基线；构建清单和 ZIP 格式参考官方 TypeScript Pro。观测条件模型预测全部已分配目标未来 15/30/60 分钟的条件，结果保存在 `agent.modelSession.latestConditions`；仪器模型接收真实公开反馈，按冻结的阈值独立发出故障申报信号，结果保存在 `latestFault`。观测条件如何用于改变选址和曝光，还需后续设计及评估。调用方式及新增模型接口见 [MODELS.md](MODELS.md)。

## 构建和运行

在本目录使用 Node.js 22：

```sh
npm run models:setup # 比赛 Linux x86_64；本机开发可设置 BASE_MODEL_PYTHON
npm ci --include=dev
npm run build
npm start
```

输入和输出均为每行一个 JSON。比赛回复写到标准输出，诊断及 `SURVEY26_INPUT_V1` 原文日志写到标准错误。保留整个输入行，没有新增长度截断。可继续通过 `BASE_KAPPA`、`BASE_K_MAX` 等 `BASE_<参数大写>` 环境变量使用原来的参数。

`BASE_CONDITIONS_MODELS=off` 与 `BASE_FAULT_MODEL=off` 分别关闭两个模型；默认同时启用。`BASE_FAULT_GUARD=rule` 让故障申报同时需要原规则检查允许；默认不需要。`BASE_MODELS_LOG=full` 输出完整观测预测。标准错误的 `SURVEY26_MODELS_V1` 与 `SURVEY26_FAULT_MODEL_V1` 记录实际调用与结果；模型进程的 CPU 用时计入现有运行节奏。

平台按 `observer.project.json` 使用 `node:22-slim` 构建。npm 缓存在 `/workspace/.npm-cache`，Python 及依赖在 `/workspace/.runtime`；智能体运行不写根目录或项目文件。资源限制见仓库根目录的 `docs/participant-runtime-limits.zh.md`。

## 打包

```sh
npm run pack
```

输出 `artifacts/baseline-typescript-v1.zip`，也可指定 `npm run pack -- --out /absolute/path/agent.zip`。

打包器收集清单、锁定依赖文件、编译设置、TS 源码、两个选定模型的权重、清单、公开推理代码、构建安装器和调用示例，以及 README 和来源说明；评测数据、结果、训练工具、Python 环境、`node_modules`、`dist`、`.env` 均不进入 ZIP。平台构建时生成 `dist`。打包时检查 50 MB ZIP、100 MB 解压大小及 10,000 文件上限。

## 本地对照

全部使用正式公开输入加自行生成的天气、事件和请求；它们不代表比赛隐藏数据。不会读取、下载或恢复官方练习卡。

以下是原迁移阶段的对照工具，默认关闭本次新增模型，以复现迁移记录。开启模型的整场成绩尚未核查。运行本次模型时，CPU 统计也应包含 Python 子进程。

从本目录运行两版完整比较：

```sh
BASE_CONDITIONS_MODELS=off BASE_FAULT_MODEL=off python3.12 tools/run_comparison.py \
  --node /absolute/path/to/node22 \
  --cards-root ../../evaluation_assets/formal-v4-feedback-self-generated-v3/nominal/cards \
  --out results/nominal-v3
```

两版均使用原来的运行节奏和官方本地引擎。记录得分、实际 CPU 秒、引擎折算的预算和完整结束原因；实际 CPU 时间只统计智能体处理请求的时间。整场经过时间还包含引擎记账与评分，应分开解读。

Python 对照使用 `SOURCE_MANIFEST.json` 记录的源码快照，自动恢复到本项目 `results/python-source-snapshot`，不会修改原 Python 项目。这样原项目继续开发时，本次比较仍能复现。

检查同一批反馈的状态更新：

```sh
BASE_CONDITIONS_MODELS=off BASE_FAULT_MODEL=off python3.12 tools/compare_migration.py \
  --node /absolute/path/to/node22 \
  --cards-root ../../evaluation_assets/formal-v4-feedback-self-generated-v3/nominal/cards \
  --feedback-only --out results/feedback-nominal-v3
```

这个独立对照工具将两版搜索范围固定为 14，并让 TS 读取 Python 的曝光记录来解释同一份反馈，检查得分、完成条件、天气/仪器状态、报告、指向偏差、请求和账本撤销。它也记录动作差异。**这是状态更新比较，不是 TS 的独立得分；工具的设置不会进入实际智能体。** 不加 `--feedback-only` 时严格比较动作，并在第一次分歧处保存现场。

Python 和 JavaScript 的三角、指数等计算可能有极小的末尾差异。当前基线在部分几乎并列的候选间直接比较小数，这会改变后续搜索起点；不能把迁移描述为每一步完全相同。实际影响以两版独立跑完的分数为依据。

本次结果和验证限制见 `MIGRATION.md`；具体输入、分数和现场保存在 `results/`。

## 来源

迁移依据是当前 Python 基线 v1.0（运行模块来自提交 `2b5ae76`，入口包含后来新增的完整输入日志）。`SOURCE_MANIFEST.json` 记录本次读取的源文件散列值。

构建和 ZIP 文件格式参考 [GOSIM 2026 Agentic Observer Hackathon](https://create.gosim.org/survey26/) 的 `examples/typescript-pro`。官方提供材料的许可见 `NOTICE.md`；参赛者自行编写的基线代码归其作者所有。
