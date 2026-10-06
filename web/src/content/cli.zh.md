# survey26 命令行工具

`survey26` 是 GOSIM 2026 智能体巡天黑客松（Agentic Observer Hackathon）的官方命令行工具。参赛者可以在终端或通过编程智能体完成网站提供的全部操作：个人资料、队伍、队伍变量与允许访问的域名、项目版本、评测、结果、最终版本、排行榜以及 Kimi Coding Plan 兑换码。工具以你的账号身份操作，权限、每日次数和配额与网站完全相同。

## 1. 安装

工具提供两种等效的构建，命令、选项、`--json` 输出和退出码完全相同（版本 1.4.0）：一个 Python 单文件，需要 Python 3.9 或更高版本、不依赖其他软件包；以及一个预编译的单文件程序，无需任何运行环境。

```bash
# 方式 A：安装 survey26 命令
pip install "git+https://github.com/gosimfoundation/hackathon-survey26#subdirectory=cli"

# 方式 B：直接下载单个文件
curl -fsSLO https://create.gosim.org/survey26/platform/survey26.py
python3 survey26.py --help
```

**方式 C：预编译程序。** 下载对应系统的文件，赋予执行权限并放入 `PATH` 即可使用。校验和：[SHA256SUMS](https://github.com/gosimfoundation/hackathon-survey26/releases/download/cli-v1.4.0/SHA256SUMS)。

| 系统 | 下载 |
| --- | --- |
| macOS（Apple 芯片） | [survey26-macos-arm64](https://github.com/gosimfoundation/hackathon-survey26/releases/download/cli-v1.4.0/survey26-macos-arm64) |
| macOS（Intel） | [survey26-macos-x86_64](https://github.com/gosimfoundation/hackathon-survey26/releases/download/cli-v1.4.0/survey26-macos-x86_64) |
| Linux x86_64（静态链接） | [survey26-linux-x86_64](https://github.com/gosimfoundation/hackathon-survey26/releases/download/cli-v1.4.0/survey26-linux-x86_64) |
| Linux aarch64（静态链接） | [survey26-linux-aarch64](https://github.com/gosimfoundation/hackathon-survey26/releases/download/cli-v1.4.0/survey26-linux-aarch64) |
| Windows x86_64 | [survey26-windows-x86_64.exe](https://github.com/gosimfoundation/hackathon-survey26/releases/download/cli-v1.4.0/survey26-windows-x86_64.exe) |

```bash
curl -fsSL -o survey26 https://github.com/gosimfoundation/hackathon-survey26/releases/download/cli-v1.4.0/survey26-linux-x86_64
chmod +x survey26 && ./survey26 --version   # survey26 1.4.0
```

在 macOS 上，用浏览器下载的程序需先解除隔离：`xattr -d com.apple.quarantine survey26`。在 Windows 上，请在 PowerShell 或命令提示符中运行 `survey26-windows-x86_64.exe`。

`survey26 --help` 列出全部命令，`survey26 <命令> --help` 说明每条命令的用法。面向编程智能体的使用说明可下载为 [survey26-AGENTS.md](__BASE_URL__survey26-AGENTS.md)，放入项目目录后，编程智能体即可读取。

## 2. 个人 API 令牌

1. 在网站登录后打开 **个人资料 → 个人 API 令牌**，填写名称（例如 `laptop` 或 `agent`）并创建令牌。令牌以 `s26_` 开头，只显示这一次；网站只保存它的指纹。
2. 建议通过环境变量把令牌提供给工具：

```bash
export SURVEY26_TOKEN=s26_...
survey26 whoami
```

也可以运行 `survey26 login --token-stdin`，从标准输入读取令牌并保存到 `~/.config/survey26/config.json`（仅本人可读）；`survey26 logout` 删除本机保存的令牌。

3. 令牌代表你本人。凡是你的账号在网站上不能做的操作，令牌同样不能做；任何账号都不能通过令牌使用主办方功能。每个账号最多同时持有 5 个有效令牌，个人资料页会显示每个令牌的创建时间和最近使用时间。令牌不再需要或可能泄露时，请立即在该页撤销，撤销后立即失效。每个账号每分钟最多 120 次请求。
4. 任何拿到令牌的人都能以你的身份操作。请勿把令牌写入仓库、项目 ZIP、日志、截图或聊天记录。令牌不能用来创建或撤销令牌，这只能在个人资料页完成。

## 3. 命令一览

| 网站功能 | 命令 |
| --- | --- |
| 账号与个人资料 | `whoami`、`profile show`、`profile set --nickname … --github …`、`profile avatar set 文件`、`profile avatar clear` |
| 找队友 | `teammates list [--looking]`、`teammates visibility show\|on\|off [--blurb … --contact … --seeking …]`、`teammates contact 用户ID`、`teammates invite 用户ID` |
| 队伍 | `team show`、`team members`、`team create 队名 [--max-size N]`、`team join 邀请码`、`team leave`、`team code [--regenerate]`、`team set --name … --max-size … --lock/--unlock`、`team transfer 用户ID`、`team kick 用户ID`、`team disband`、`team directory`、`team request 队伍ID`、`team invite-uid UID`（队长） |
| 好友 | `friends list`（你的 UID、好友、请求、已屏蔽）、`friends add UID`、`friends accept ID`、`friends decline ID`、`friends cancel ID`、`friends remove 用户ID`、`friends block 用户ID`、`friends unblock 用户ID` |
| 消息通知 | `invites list`、`invites accept ID`、`invites decline ID`、`invites cancel ID` |
| 密钥与网络 | `env show`、`env model --provider kimi\|moonshot\|deepseek\|openai\|anthropic\|zhipu\|custom --key - [--model …] [--prefix 名称] [--replace]`（即「添加模型服务」）、`env set 名称 --value-stdin [--plain]`、`env set 名称 --from-env 变量`、`env unset 名称`、`env disable 名称` / `env enable 名称`（停用/启用，保留不删除）、`env tag 名称 model\|none`（是否模型相关）、`env domains set 域名…`、`env domains clear`、`env route [direct\|cn\|overseas] [--fallback\|--no-fallback]` |
| 第 1 步 · 上传项目 | `project upload 文件.zip [--title …]`、`project submit-repo https://github.com/OWNER/REPO [--branch 分支] [--subdir 子目录] [--title …]`（也可以直接用 `…/tree/分支/子目录` 或 `…/commit/提交号` 链接；提交时记录具体 commit） |
| 第 2 步 · 检查并确认版本 | `project list [--all]`、`project wait 版本`、`project show 版本 --files`、`project logs 版本`、`project confirm 版本`、`project withdraw 版本`、`project download 版本`、`project evidence 版本 --notes … --code-url …` |
| 第 3 步 · 开始评测 | `quota`、`eval start 版本 [--no-model]`、`eval selfcheck 版本 [--no-model]`（评测 3 次取平均；`--no-model` 即「本次不提供模型」）、`eval list`、`eval show 评测`、`eval wait [评测]` |
| 结果 | `results show 评测`、`results log 运行 [--tail N \| --full \| -o agent.log]`、`results download 运行`、`results download-all [评测]` |
| 最终版本 | `final show`、`final set 版本`、`final clear` |
| 排行榜 | `leaderboard [--phase online\|practice-projects\|practice] [--card v4-a] [--mine]`、`competition` |
| Kimi Coding Plan 与兑换码 | `kimi status`、`kimi claim`（队长）、`credits list`、`credits claim 提供方` |

“版本”（项目版本）、“评测”（一次评测）和“运行”（一次评测中的一张任务卡）可以填写完整 ID，也可以填写至少 4 位、能唯一匹配的前缀，即 `project list`、`eval list`、`eval show` 输出中的 ID。`latest` 表示最近一次评测。

## 4. 完整流程示例

```bash
survey26 env model --provider kimi --key - < key.txt       # 写入 OPENAI_API_KEY（密文）、OPENAI_BASE_URL、OPENAI_MODEL
survey26 project upload agent.zip --title "my agent"        # 占用每天 10 次上传中的 1 次
survey26 project wait 1a2b3c4d                              # 等待准备和公开场景测试；失败时退出码为 9
survey26 project logs 1a2b3c4d                              # 构建日志与公开测试的 agent.log
survey26 project show 1a2b3c4d --files                      # 运行设置与适配文件
survey26 project confirm 1a2b3c4d --yes                     # 不占评测次数
survey26 eval start 1a2b3c4d --yes                          # 占用今天 1 次评测
survey26 eval wait --timeout 3600                           # 结束后显示各卡分数
survey26 results log 运行ID --tail 100
survey26 results download-all latest -o results.zip
survey26 final set 1a2b3c4d
```

`env model` 与网站「添加模型服务」表单写入相同的三个变量，使用相同的服务商预设：密钥以密文保存，接口地址和模型名以明文保存。不加 `--prefix` 时变量名为 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`（Anthropic 为 `ANTHROPIC_*`）；加 `--prefix KIMI` 时为 `KIMI_API_KEY` 等，便于同时配置多个服务商。已存在的变量不会被覆盖，除非加 `--replace`（否则退出码为 2）。`--key -` 从标准输入读取密钥，避免密钥出现在 shell 历史中。

`env route` 查看或设置本队评测的出网线路，与「密钥与网络」中的「出网线路」是同一项设置：`direct`（直连，默认）、`cn`（回国代理）或 `overseas`（海外代理）。选择线路后，程序的对外连接经平台提供的该线路发出；加 `--fallback`（默认）时线路不可用则改为直连，加 `--no-fallback` 时则拒绝该连接。运行的网络记录会注明每个访问地址走的线路。

## 5. 输出与退出码

所有命令都支持 `--json`，此时标准输出只有一个 JSON 对象：

```json
{"ok": true, "command": "eval start", "data": {"batch_id": "…", "phase": "online", "revision_id": "…", "repeat": false}}
{"ok": false, "command": "eval start", "error": {"code": "daily_limit", "message": "今天的评测次数已用完。", "exit_code": 8}}
```

`error.code` 与网站使用的错误代码相同，`error.message` 是网站上的提示文字（中文或英文，由 `--lang` 或 `SURVEY26_LANG` 决定）。

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 1 | 服务器拒绝（见 `error.code`） |
| 2 | 用法错误、ID 前缀不唯一，或需要确认（`--yes`） |
| 3 | 缺少令牌、令牌无效或已撤销、账号已停用 |
| 4 | 找不到对象 |
| 5 | 请求过于频繁，请等待一分钟 |
| 6 | 网络或服务器暂时不可用，请稍后重试 |
| 7 | `wait` 命令达到 `--timeout` |
| 8 | 达到每日或队伍上限（评测次数、上传次数、已有进行中的评测、队伍人数、每天 20 次 UID 操作） |
| 9 | 所等待的准备或评测未能成功完成 |

网站上会弹出确认框的操作（再次评测同一版本、评测 3 次取平均、撤回版本、取消最终版本选择、退出队伍、转让队长、移出队员、解散队伍、几分钟内重复提交同一项目），在交互式终端中同样需要确认。使用 `--json` 或没有终端时，工具从不等待输入：这些操作必须加 `--yes`，否则以退出码 2 结束。`project wait` 和 `eval wait` 默认每 15 秒和 20 秒查询一次（`--interval`，最少 5 秒），超过 `--timeout` 秒后停止。

## 6. 编程智能体使用须知

1. 从 `SURVEY26_TOKEN` 读取令牌，不得打印、记录、提交令牌，也不得把令牌放进项目 ZIP。
2. 使用 `--json`，并根据 `ok`、`error.code` 和退出码决定下一步。
3. 每次 `eval start` 占用本队当天 1 次评测，`eval selfcheck` 占用 3 次。只评测已通过公开场景测试的版本，不要在无人值守的循环中反复评测，评测次数的使用请与队友商定。
4. 用 `env set 名称 --value-stdin` 或 `--from-env 变量` 设置密钥，不要把密钥写在命令参数中，以免出现在 shell 历史或进程列表里。
5. 用 `project wait`、`eval wait` 及其 `--timeout` 等待，不要高频轮询。退出码 5 表示应等待一分钟，退出码 6 表示应稍后重试。
6. 执行 `project confirm` 之前，请先用 `project show 版本 --files` 检查：确认即表示本队已检查运行设置和适配文件。
7. 评测过程中的每个决策都必须由程序自动做出；关于人工参与的规则同样适用于命令行。

## 7. 命令行不提供的功能

- 注册、登录、修改和重置密码：只能在网站上完成，API 令牌不能执行这些操作。
- 创建和撤销 API 令牌：只能在个人资料页完成，以免泄露的令牌再生成新的令牌。
- 主办方功能：任何账号都不能通过令牌使用。
- 原有的队伍模型 API 设置和浏览器内模型中转：已由 **密钥与网络**（`survey26 env`）取代。
- 本地 CSV 会话以及早期练习赛的 CSV 提交记录：这些形式已不再接受。
