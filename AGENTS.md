# Event repository

This repository owns https://create.gosim.org/survey26/.
Read MAINTAINING.md before changing deployment or routing.
Use Node.js 22. Keep the existing public base path and authentication callbacks.
Website changes belong here; the shared domain publisher lives in
`gosimfoundation/hackathon`. Run the production build before merging.
Do not run database migrations or redeploy backend workers as part of a website-only change.

## Harness

- 开展新实验时先从小规模设置开始，确认方法可行后再逐步扩大；不要一开始就采用很大的数据量或运行规模。
- 后续智能体本地测试默认使用 `evaluation_assets/formal-v4-observed-timeline-v4` 的 A–D 卡；它恢复已收到的正式公告与请求，隐藏天气仍属标注假设，只用于本地比较，不将得分视为线上预测。旧评测集保留，切换测试集时明确说明。
- 本项目的智能体开发和本地评测只使用官方正式比赛卡，或自行生成并明确标注来源的数据；不使用、下载或恢复官方练习卡（包括 L1–L4 和 α–δ）。
- 正式比赛卡的公开输入保持原样；为本地评测补充的天气、事件和请求单独存放，并明确说明它们是自行生成的，不代表正式比赛隐藏数据。
- 观测条件模型负责提炼客观信息，为智能体推理提供参考；输出优先保留有用信息、适用范围和不确定性，不默认替智能体选择、排序截断或排除观测方案。具体策略由智能体结合任务目标判断。
- 观测条件模型的评估优先检查信息可信度：预测范围与概率是否符合实际、哪些结论有依据、哪些条件下信息不足；单点预测误差作为辅助指标，不以缩小候选数量或直接选中最佳方案作为首要目标。
- 观测条件模型后续架构比较优先考虑处理历史顺序的模型，同时保留普通全连接网络或树模型作为参照；使用相同公开信息、数据划分和评估标准比较，按实测结果判断历史与顺序处理的价值。
- 正式参赛项目须遵守 `docs/participant-runtime-limits.zh.md` 中每张卡的容器资源、只读根目录、可写目录、构建时限和项目大小限制；大型工具或依赖可在 `observer.project.json` 的 `build` 步骤中联网安装。
