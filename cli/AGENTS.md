<!-- Generated from web/src/content/cli.{en,zh}.md by cli/build_agents_md.py; edit those files. -->

# survey26 command-line tool

`survey26` is the official command-line tool of the GOSIM 2026 Agentic Observer Hackathon. It performs, from a terminal or from a coding agent, every action that the website offers to a contestant: profile, team, team variables, project versions, evaluations, results, the final version, the leaderboard and the Kimi Coding Plan code. It acts with your account and is subject to exactly the same permissions, daily limits and quotas as the website.

## 1. Installation

The tool is available in two equivalent builds with the same commands, options, `--json` output and exit codes (version 1.4.0): a single Python file that requires Python 3.9 or later and no other packages, and a single prebuilt binary that requires nothing at all.

```bash
# Option A: install the survey26 command
pip install "git+https://github.com/gosimfoundation/hackathon-survey26#subdirectory=cli"

# Option B: download the single file
curl -fsSLO https://create.gosim.org/survey26/platform/survey26.py
python3 survey26.py --help
```

**Option C: prebuilt binary.** Download the file for your system, make it executable and place it on your `PATH`. Checksums: [SHA256SUMS](https://github.com/gosimfoundation/hackathon-survey26/releases/download/cli-v1.4.0/SHA256SUMS).

| System | Download |
| --- | --- |
| macOS (Apple silicon) | [survey26-macos-arm64](https://github.com/gosimfoundation/hackathon-survey26/releases/download/cli-v1.4.0/survey26-macos-arm64) |
| macOS (Intel) | [survey26-macos-x86_64](https://github.com/gosimfoundation/hackathon-survey26/releases/download/cli-v1.4.0/survey26-macos-x86_64) |
| Linux x86_64 (static) | [survey26-linux-x86_64](https://github.com/gosimfoundation/hackathon-survey26/releases/download/cli-v1.4.0/survey26-linux-x86_64) |
| Linux aarch64 (static) | [survey26-linux-aarch64](https://github.com/gosimfoundation/hackathon-survey26/releases/download/cli-v1.4.0/survey26-linux-aarch64) |
| Windows x86_64 | [survey26-windows-x86_64.exe](https://github.com/gosimfoundation/hackathon-survey26/releases/download/cli-v1.4.0/survey26-windows-x86_64.exe) |

```bash
curl -fsSL -o survey26 https://github.com/gosimfoundation/hackathon-survey26/releases/download/cli-v1.4.0/survey26-linux-x86_64
chmod +x survey26 && ./survey26 --version   # survey26 1.4.0
```

On macOS, a binary downloaded with a browser must first be released from quarantine: `xattr -d com.apple.quarantine survey26`. On Windows, run `survey26-windows-x86_64.exe` from PowerShell or the command prompt.

`survey26 --help` lists all commands; `survey26 <command> --help` describes each one. The usage guide for coding agents can be downloaded as [survey26-AGENTS.md](https://create.gosim.org/survey26/platform/survey26-AGENTS.md); place it in your project so that your coding agent reads it.

## 2. Personal API token

1. Log in on the website and open **Profile → Personal API tokens**. Enter a name (for example `laptop` or `agent`) and create a token. The token begins with `s26_` and is shown only once; the website stores only a fingerprint of it.
2. Make the token available to the tool, preferably through the environment:

```bash
export SURVEY26_TOKEN=s26_...
survey26 whoami
```

Alternatively, `survey26 login --token-stdin` reads the token from standard input and stores it in `~/.config/survey26/config.json` (readable only by you). `survey26 logout` removes the stored copy.

3. A token acts as you. It cannot do anything that your account cannot do on the website, and organizer functions are never available with a token. Each account may have up to 5 active tokens; the profile page shows when each token was created and last used. Revoke a token there as soon as it is no longer needed or may have been exposed: it stops working immediately. Requests are limited to 120 per minute per account.
4. Anyone who has your token can act as you. Do not put tokens in repositories, project ZIPs, logs, screenshots or chats. A token cannot be used to create or revoke tokens; this is done only on the profile page.

## 3. Commands

| Website | Command |
| --- | --- |
| Account and profile | `whoami`, `profile show`, `profile set --nickname … --github …`, `profile avatar set FILE`, `profile avatar clear` |
| Find teammates | `teammates list [--looking]`, `teammates visibility show\|on\|off [--blurb … --contact … --seeking …]`, `teammates contact USER_ID`, `teammates invite USER_ID` |
| Team | `team show`, `team members`, `team create NAME [--max-size N]`, `team join CODE`, `team leave`, `team code [--regenerate]`, `team set --name … --max-size … --lock/--unlock`, `team transfer USER_ID`, `team kick USER_ID`, `team disband`, `team directory`, `team request TEAM_ID`, `team invite-uid UID` (captain) |
| Friends | `friends list` (your UID, friends, requests, blocked), `friends add UID`, `friends accept ID`, `friends decline ID`, `friends cancel ID`, `friends remove USER_ID`, `friends block USER_ID`, `friends unblock USER_ID` |
| Notifications | `invites list`, `invites accept ID`, `invites decline ID`, `invites cancel ID` |
| Keys and network | `env show`, `env model --provider kimi\|moonshot\|deepseek\|openai\|anthropic\|zhipu\|custom --key - [--model …] [--prefix NAME] [--replace]` (the “Add a model service” form), `env set NAME --value-stdin [--plain]`, `env set NAME --from-env VAR`, `env unset NAME`, `env disable NAME` / `env enable NAME` (switch off/on, kept), `env tag NAME model\|none` (model-related or not), `env domains set HOST…`, `env domains clear`, `env route [direct\|cn\|overseas] [--fallback\|--no-fallback]` |
| Step 1 · Upload a project | `project upload FILE.zip [--title …]`, `project submit-repo https://github.com/OWNER/REPO [--branch BRANCH] [--subdir FOLDER] [--title …]` (a `…/tree/BRANCH/FOLDER` or `…/commit/SHA` link works too; the exact commit is saved at submission) |
| Step 2 · Review and confirm | `project list [--all]`, `project wait REV`, `project show REV --files`, `project logs REV`, `project confirm REV`, `project withdraw REV`, `project download REV`, `project evidence REV --notes … --code-url …` |
| Step 3 · Evaluate | `quota`, `eval start REV [--no-model]`, `eval selfcheck REV [--no-model]` (evaluate 3 times and average; `--no-model` = “this evaluation without a model”), `eval list`, `eval show BATCH`, `eval wait [BATCH]` |
| Results | `results show BATCH`, `results log RUN [--tail N \| --full \| -o agent.log]`, `results download RUN`, `results download-all [BATCH]` |
| Final version | `final show`, `final set REV`, `final clear` |
| Leaderboard | `leaderboard [--phase online\|practice-projects\|practice] [--card v4-a] [--mine]`, `competition` |
| Kimi Coding Plan and credits | `kimi status`, `kimi claim` (captain), `credits list`, `credits claim PROVIDER` |

`REV` (a project version), `BATCH` (an evaluation) and `RUN` (one card of an evaluation) accept the full ID or a unique prefix of at least 4 characters, as printed by `project list`, `eval list` and `eval show`. `latest` refers to the most recent evaluation.

## 4. A complete workflow

```bash
survey26 env model --provider kimi --key - < key.txt       # OPENAI_API_KEY (secret), OPENAI_BASE_URL, OPENAI_MODEL
survey26 project upload agent.zip --title "my agent"        # uses 1 of the 10 daily uploads
survey26 project wait 1a2b3c4d                              # preparation and public test; exit 9 if it failed
survey26 project logs 1a2b3c4d                              # build log and agent.log of the public test
survey26 project show 1a2b3c4d --files                      # execution settings and adapter files
survey26 project confirm 1a2b3c4d --yes                     # does not use an evaluation
survey26 eval start 1a2b3c4d --yes                          # uses 1 of today's evaluations
survey26 eval wait --timeout 3600                           # scores per card when finished
survey26 results log RUN_ID --tail 100
survey26 results download-all latest -o results.zip
survey26 final set 1a2b3c4d
```

`env model` writes the same three variables as the website's **Add a model service** form, with the same provider presets: the key as a secret, the base URL and the model as plain values. Without `--prefix` the names are `OPENAI_API_KEY`, `OPENAI_BASE_URL` and `OPENAI_MODEL` (`ANTHROPIC_*` for Anthropic); with `--prefix KIMI` they are `KIMI_API_KEY` and so on, so that several providers can be configured side by side. Existing variables are not overwritten unless `--replace` is given (exit code 2 otherwise). `--key -` reads the key from standard input so that it does not appear in the shell history.

`env route` shows or sets the team's egress route for evaluations, the same setting as **Egress route** under Keys and network: `direct` (default), `cn` (China route) or `overseas` (overseas route). With a route, the program's outbound connections go through that platform route; with `--fallback` (the default) a connection goes direct when the route is unavailable, with `--no-fallback` it is refused instead. The run's network record shows the path each destination took.

## 5. Output and exit codes

Every command accepts `--json` and then prints exactly one JSON object on standard output:

```json
{"ok": true, "command": "eval start", "data": {"batch_id": "…", "phase": "online", "revision_id": "…", "repeat": false}}
{"ok": false, "command": "eval start", "error": {"code": "daily_limit", "message": "The daily evaluation limit has been reached.", "exit_code": 8}}
```

`error.code` is the code the website uses; `error.message` is the website's message (English or Chinese, chosen by `--lang` or `SURVEY26_LANG`).

| Exit code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Refused by the server (see `error.code`) |
| 2 | Usage error, ambiguous ID, or confirmation required (`--yes`) |
| 3 | Missing, invalid or revoked token; account suspended |
| 4 | Not found |
| 5 | Too many requests; wait one minute |
| 6 | Network or server temporarily unavailable; retry later |
| 7 | A `wait` command reached its `--timeout` |
| 8 | A daily or team limit was reached (evaluations, uploads, active evaluation, team size, 20 actions by UID per day) |
| 9 | The awaited preparation or evaluation finished unsuccessfully |

Actions that the website confirms with a dialog (evaluating a version again, the 3-evaluation self-check, withdrawing a version, clearing the final version, leaving, transferring, removing members, disbanding, submitting the same project again within a few minutes) ask for confirmation in an interactive terminal. With `--json` or without a terminal the tool never prompts: such actions fail with exit code 2 unless `--yes` is given. `project wait` and `eval wait` check the status every 15 and 20 seconds by default (`--interval`, at least 5) and stop after `--timeout` seconds.

## 6. Guidance for coding agents

1. Read the token from `SURVEY26_TOKEN`. Never print, log, commit or include it in a project ZIP.
2. Use `--json` and act on `ok`, `error.code` and the exit code.
3. Each `eval start` uses one of the team's evaluations for the day, and `eval selfcheck` uses three. Evaluate only versions that passed their public test, never in an unattended loop, and agree the evaluation budget with your team.
4. Set secrets with `env set NAME --value-stdin` or `--from-env VAR`, not as command arguments, so that they do not appear in shell history or process lists.
5. Use `project wait`, `eval wait` and their `--timeout` instead of polling in a tight loop. Exit code 5 means waiting one minute; exit code 6 means retrying later.
6. Before `project confirm`, inspect `project show REV --files`: confirming states that your team reviewed the execution settings and adapter files.
7. Every decision during an evaluation must be made by your program; the rules on human participation apply in the same way to the command line.

## 7. Not available from the command line

- Registration, login, password change and password reset: these require the website and are not possible with an API token.
- Creating and revoking API tokens: only on the profile page, so that a leaked token cannot create further tokens.
- Organizer functions: never available with a token, also for organizer accounts.
- The former team model API settings and the in-browser model relay: superseded by **Keys and network** (`survey26 env`).
- The local CSV session and the CSV submission history of the earlier practice round: these formats are no longer accepted.

---

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

`survey26 --help` 列出全部命令，`survey26 <命令> --help` 说明每条命令的用法。面向编程智能体的使用说明可下载为 [survey26-AGENTS.md](https://create.gosim.org/survey26/platform/survey26-AGENTS.md)，放入项目目录后，编程智能体即可读取。

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
