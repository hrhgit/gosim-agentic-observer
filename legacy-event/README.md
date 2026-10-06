# 巡天智能体 · Agent Observer

官网保留赛事介绍、赛程、奖金和现有视觉设计。报名、登录、组队、提交、开发资料与赛事通知
统一接入 https://bh3gei.github.io/agent-observer/ 。平台源码：https://github.com/BH3GEI/agent-observer 。

## 本地开发

从仓库根目录运行 `npm run dev:agentic-cosmos-2026`。
构建检查：`npm run build --prefix events/agentic-cosmos-2026`。
中英文文案在 `src/i18n/zh.ts` 与 `src/i18n/en.ts`，平台地址集中在 `src/registration.ts`。

## 平台接入

- 所有报名按钮和旧 `/register` 地址指向平台 `/register`。
- 登录、队伍管理、提交、入门工具包、开发文档和赛事通知入口位于首页参赛区。
- 用户在平台完成 Supabase Auth 登录；账号、队伍、存储与评测继续使用该平台已有的 Supabase 项目和 worker。
- 官网仅调用匿名、允许 CORS 的公开 leaderboard Edge Function；不需要 anon key 或 service role key。
- 首页展示 `phase=online` 的正式榜，避免把练习成绩当作正式比赛成绩；各阶段榜单可在平台查看。
- 正式比赛提交智能体程序包（当前平台 Python 3.12）；练习支持结果文件或程序包。接口、计分和通知以平台为准。

默认地址开箱可用。可通过 `VITE_AGENT_OBSERVER_URL` 和 `VITE_AGENT_OBSERVER_LEADERBOARD_API` 覆盖。
根目录构建脚本对应 `COSMOS_VITE_AGENT_OBSERVER_URL`、`COSMOS_VITE_AGENT_OBSERVER_LEADERBOARD_API`；
GitHub Pages workflow 读取同名 repository variables。未配置时使用源码中的已验证默认值。

## 旧报名数据与数据库

腾讯问卷不再作为官网报名入口。既有问卷记录不会自动成为 Supabase Auth 账号；
已填问卷的参与者需要在比赛平台注册账号并创建或加入队伍。
如果主办方需要导入既有资料，应先取得问卷导出文件并与平台维护者核对字段和处理方式；
不要自动创建密码、默认认领邮箱或把问卷答案直接写入公开资料表。

本目录旧 `RegisterPage.vue` 和 `supabase/migrations/` 属于之前的独立报名实现，当前路由不再使用。
不要把这些迁移应用到 BH3GEI 平台的现有数据库；平台 schema 由其自身仓库维护。
不需要更改平台 Supabase 的 Auth 回调域名，因为注册和登录继续发生在平台原域名。
