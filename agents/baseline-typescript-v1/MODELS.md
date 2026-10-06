# TypeScript 基线的两个模型

## 当前默认版本

| 用途 | 模型 ID | 来源与版本 |
| --- | --- | --- |
| 观测条件与可信度 | `conditions-reliability` | `artifacts/observing_conditions-reliability/selected`，10 月 5 日 **11:44** 冻结；`gru64/fresh_hybrid_12/endpoint_raw`；版本 `conditions-reliability-v2-9138b8ec1431a1e2` |
| 仪器故障申报 | `instrument-fault-report` | `artifacts/instrument_faults-report-model`；已训练的 415 输入、300 棵树模型；免费阈值 0.5、付费阈值 0.9 |

之前接入的信息提炼版 GRU/MLP 仅保留为本地历史对照，默认调用及提交包均使用上表的两个模型。没有重新训练。观测模型的原始权重、标准化、历史反馈修正、范围和概率调整以及有效比例 0/1 端点预测全部保留。故障模型按原树节点导出，不需要在运行时安装训练工具。

两个模型共享一个常驻 Python 进程，TypeScript 顺序发送请求，权重只加载一次。智能体的搜索、状态管理和比赛入口仍使用 Node.js 22；Python 3.12、CPU 版 PyTorch、NumPy、SciPy 在比赛构建阶段安装到 `.runtime/`。没有新增模型调用超时、输入长度上限或失败时的假预测。加载及推理错误会明确记录。

## 基线如何调用

- 每次决策更新已经公开的公告、预报和已完成观测反馈。
- 每次生成观测动作，为全部已分配目标预测未来 **15、30、60 分钟**的观测条件。该版本未训练当前时刻的预测，接口拒绝当前时刻；也支持下一晚开始时刻的独立调用。
- 完整观测预测保存在 `agent.modelSession.latestConditions`，保留全部候选、范围、概率、历史充分程度及证据限制。目前选址和曝光仍由原搜索规则生成，后续可以据此设计策略。
- 仪器模型在收到反馈后计算申报评分，结果保存在 `agent.modelSession.latestFault`。每三次新的有效观测才进入申报判断，默认只检查免费/付费阈值，由模型独立发出申报信号；`BASE_FAULT_GUARD=rule` 恢复“同时要求原天气/仪器检查允许申报”。模型的 415 个输入里仍有 9 个取自基线估计器的状态（天气水平、效率、下降前效率及两者之比、仪器怀疑标记、低效率次数与时长、关闭怀疑、档位探测），去掉它们需要重新训练。原基线的最后免费检查仍保留。
- 正确及错误申报后都清除旧故障证据。八小时冷却属于原检查：独立申报时冷却剩余时间只作为模型输入，`BASE_FAULT_GUARD=rule` 时才会拦住申报。数据撤销后移除对应反馈，并清除依赖旧观测的仪器怀疑。观测模型保留最近八次有效历史；较早历史的存在与当晚历史数量分别表达。

`report_score` 是考虑申报收益与代价的评分，**不是经过可信度调整的故障概率**。没有观测时返回 `null`，历史不足时明确说明；不会拿 0 或某个固定概率冒充推理结果。观测模型的完整可信度核查仍为 `false`，故障模型尚无独立最终核查；这些状态随输出保留。

可用 `BASE_CONDITIONS_MODELS=off` 或 `BASE_FAULT_MODEL=off` 分别关闭两个模型。`BASE_FAULT_GUARD` 取 `off`（默认，模型独立申报）或 `rule`。观测模型也接受 `BASE_CONDITIONS_MODELS=reliability`。`BASE_MODELS_LOG=full` 输出完整观测预测；默认只记录调用概况和故障评分。

## 构建和直接调用

比赛 Linux x86_64 使用 Node.js 22：

```sh
npm run models:setup
npm ci --include=dev
npm run build
npm run models:predict -- --input models/example-reliability-request.json
npm start
```

在本机开发可复用已有 Python 3.12 环境：

```sh
export BASE_MODEL_PYTHON=/absolute/path/to/python
npm ci --include=dev
npm run build
npm run models:predict -- --input models/example-reliability-request.json
```

本仓库可使用 `research/observing_conditions/.venv/bin/python`。不要把本机 Python 路径写入提交清单。比赛构建自动安装 Python 3.12.11、PyTorch 2.8.0 CPU、NumPy 2.2.6、SciPy 1.15.3，运行时不需要联网，根目录和项目目录均可只读。

### 观测条件

```ts
const models = new BaselineModels();
try {
  const result = await models.predictReliability({
    initialize, now_utc, baseline_state, feedback_history,
    bulletins, forecasts, candidates,
  });
} finally {
  await models.close();
}
```

`baseline_state`、`feedback_history` 必须来自已公开的反馈；格式与原 `ReliabilityPredictor` 相同。候选曝光长度为 60–3600 秒，开始时刻为未来 15/30/60 分钟或下一晚开始。未知字段、无效坐标和范围外的输入明确报错。夜外或整次曝光低于公开最低高度的候选仍保留，并说明无法观测的原因。

### 同时调用两个模型

```ts
const models = new BaselineModels();
const session = new SelectedModelsSession(initialize, models);
try {
  await session.update({
    now_utc, messages, completed_exposure,
    baseline_state, sky, remaining_gain,
  });
  const conditions = await session.conditions(now_utc, futureCandidates);
  const fault = await session.faults(now_utc, publicReportGuard);
} finally {
  await session.close();
}
```

每个场景使用独立 `BaselineModels` 和 `SelectedModelsSession`。`SessionUpdate`、`ReliabilityInput` 和 `FaultRequest` 的 TypeScript 定义位于 `src/models/selected.ts`；`src/agent.ts` 提供完整的反馈和仪器状态构造示例。`session.faults` 会消费到期的“三次新观测”申报机会，查看上次结果使用 `latestFault`。

独立 JSONL 入口也接受 `{operation, input}`：先发送 `initialize`（`input` 为 `{initialize,enabled}`），再发送 `sync`（`SessionUpdate`），然后发送 `fault`（`FaultRequest`）或 `session_conditions`。普通观测请求可以直接逐行发送，输出每行一个预测响应。标准输出只用于 JSON；诊断写到标准错误。

## 新增其他网络

`ModelRegistry<TaskMap>` 负责按任务和模型 ID 注册、延迟创建和实例复用。适配器只需实现 `id` 和 `predict(input)`，输出可为同步结果或 `Promise`。不同网络可以使用不同输入、运行库和进程；调用者不需要了解权重结构。

```ts
interface Tasks {
  anotherTask: {
    input: { values: number[] };
    output: Promise<{ estimate: number }>;
  };
}
const registry = new ModelRegistry<Tasks>();
registry.register("anotherTask", "new-network-v1", () => new NewAdapter());
const result = await registry.predict("anotherTask", "new-network-v1", { values: [1, 2] });
```

当前观测任务为 `reliability`，故障任务为 `instrumentFault`。新增时扩展 `BaselineModelTasks` 并注册适配器；需要常驻 Python 的网络可以复用 `PythonTransport` 的请求管理。其请求串行执行，异常会返回给调用者，关闭时结束进程。模型输入处理、输出含义和版本核对由各适配器负责。

运行资产更新使用 `tools/export_selected_models.py`；它复制冻结权重并提取公开推理代码，记录来源及文件摘要。提交包仅包含公开推理代码和必要模型资产，不含训练场景、标签、模拟器天气读取、Python 环境或 `node_modules`。

## 已核对的内容

数值与调用证据在本地 `results/selected-models/`：

- 正式 A–D 公开输入与已有自行生成场景，16 组、13,824 个候选，其中 13,042 个可观测；核对 278,150 个数值及其他输出字段，和原 Python 可信度优化版的差为 **0**。包含没有历史、部分历史、八次历史、较早历史及下一晚预测。
- 仪器模型 991 条、415 输入的预测与原已训练模型的差为 **0**，覆盖缺失输入的树分支。
- 完整 TypeScript 基线四张卡共 72 次决策、67 次观测预测，均调用故障接口；包含自行生成的低响应反馈、错误申报、冷却及数据撤销。申报后旧评分清空，三次新观测计数重新开始。
- Linux x86_64：2 核、2 GB、128 进程、只读根目录及运行目录、256 MB 临时目录、运行时禁用网络。冷启动安装约 36.4 秒，npm 安装和编译约 2.0 秒；四张卡共 72 次决策、67 次观测预测、72 次故障接口调用，62 次完成实际评分，无调用错误。
- Linux 与本机的观测质量/档位/受阻概率差小于 1e-6；有效比例下边界最大差约 0.000112，中位数最大差约 0.00000775。这些差异涉及运行库及不同 CPU 的浮点计算，原数值均保留；明细见 `linux-summary.json`。Linux 用的是 Mac 上的 x86 转译环境，不能当作正式服务器测速。

本地复查可执行现有 `tools/verify_models.py --node /absolute/path/to/node22`；`benchmark:models` 测量当前可信度优化版。旧 GRU/MLP 的数值脚本和测速入口标为 `legacy`，其历史结果不代表本次版本。

本机 Node.js 22 的当前观测模型完整调用 p95：16 个位置约 8.7 ms、100 个位置约 12.6 ms、1024 个位置约 62.2 ms；包含进程通信和结果解析，单一未来曝光，预热 5 次、测量 30 次。首次加载加调用约 681 ms。实际基线会预测三个未来时刻，不能把单一时刻测速直接当作整个决策耗时。
