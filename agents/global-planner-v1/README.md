# 全局规划实验副本

从 `baseline-typescript-v1` 独立复制，`BASELINE_SOURCE.json` 记录复制时源码和模型文件的摘要。没有修改原基线。Node.js 22，两个配套模型与来源快照原样保留。

新增内部接口 `dist/tools/planning-bridge.js`：初始化、送入公开反馈、返回候选、接收选择、返回实际动作。候选包括原规则动作，预览不会改变仪器探测状态；选择和实际执行不一致时立即报错。该接口供本地训练器调用，不是比赛 JSONL 协议。

`dist/agent.js` 保留比赛协议，默认仍运行基线规则，**没有自动启用训练出的规划权重**。运行失败会明确终止，不再以等待动作掩盖模型加载错误。新策略必须经过训练方案规定的检验才能接入或替换。

```sh
npm ci --include=dev
npm run build
npm test
```

本机调用配套模型时设置 `BASE_MODEL_PYTHON` 为已安装的 Python 3.12 环境。官方本地运行器默认清理环境，需将该设置显式写在 `--agent` 的 `env ...` 命令中；不能只在外层 shell 设置。比赛构建仍使用 `runtime/setup.cjs`。

训练与当前结果说明见仓库 `research/global_planning/README.md`。未通过门槛的研究权重不得当作已验证的正式参赛版本。
