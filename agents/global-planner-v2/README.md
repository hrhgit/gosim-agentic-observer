# 全局规划接入副本 v2

独立于 `global-planner-v1` 和原规则基线。默认 `dist/agent.js` 仍运行原规则。

研究桥接入口是 `dist/tools/planning-bridge.js`，支持区域/时间、模式和光纤目标的分层选择。每次预览和最终动作都检查一致性；程序错误直接退出，无额外等待或降级。

显式学习模型入口为 `dist/global-entry.js`。它运行本地 Python 预测进程，并使用相同的 TypeScript 动作生成器。构建用 `runtime/setup.cjs` 安装 Linux x86_64 的 CPU 依赖。配置和权重由研究导出流程保存到 `models/global-planning`，也可以明确设置 `GLOBAL_PLANNER_CONFIG`。导出模型存在并不表示它已达到采用门槛；提交清单当前仍指向规则入口。

详见 [完整实验说明](../../research/global_planning/staged/README.md)。
