# 本项目本地评测说明

已按用户要求删除 L1–L4 练习卡数据。此项目以后只使用官方正式比赛卡或自行生成的数据；下文有关 L1–L4 的说明是上游历史文档，不再适用于此工作区。保留的 `runner/` 可以用于完整的自行生成卡，正式 A–D 的公开包需另外补充自行生成的天气、事件和请求才能本地运行。

# GOSIM Agentic Observer 示例项目 / Example projects

- 每个示例项目一个文件夹（`python/`、`python-pro/`、`typescript/`、`rust/`、`rust-pro/` 等）：完整的示例智能体，各自的 README 和 AGENTS.md 说明如何配置与打包上传。
- `docs/`：参赛文档（中文 / English）。
- `local-cards/L1`–`L4`：四张完全公开的本地练习卡。
- `runner/`：本地裁判程序，与平台使用同一份评测代码（`python3 runner/verify_engine.py` 可核对）。只需要 Python 3，无需安装任何库。

## 本地跑分 / Local scoring

```
python3 runner/run_local.py --inherit-env --card local-cards/L1 --agent "python3 python/agent.py"
python3 runner/run_local.py --inherit-env --card local-cards/L1 --agent "node typescript/dist/index.js"   # 先 cd typescript && npm ci && npm run build
python3 runner/run_local.py --inherit-env --card local-cards/L1 --agent "./rust/target/release/rust-agent" # 先 cd rust && cargo build --release
```

运行前设置大模型密钥：`export OPENAI_API_KEY=...`（默认 Kimi Coding Plan，见各项目 README）。

本地分数用来调试，正式成绩以平台为准。
Local scores are for debugging; official results come from the platform.
