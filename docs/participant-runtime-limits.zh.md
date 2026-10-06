# 参赛项目运行环境与大小限制

适用于通过完整项目或 ZIP 提交、由平台为每张比赛卡构建和运行的参赛智能体。以下按本次提供的平台说明整理。

## 每张卡的运行环境

| 项目 | 限制 |
| --- | --- |
| 系统 | 独立 Linux 容器，x86_64 |
| CPU | 2 个 CPU 核 |
| 内存 | 2 GB |
| 进程数 | 最多 128 个 |
| 根目录 | 只读 |
| 可写位置 | `/workspace`（项目目录）和 `/tmp`；`/tmp` 最多 256 MB |
| 构建时间 | 最多 10 分钟 |

构建命令、程序和依赖缓存应写入容器允许写入的位置。示例清单将 npm 缓存放在 `/workspace/.npm-cache`，将 Cargo home 放在 `/tmp/cargo-home`。

## 项目提交大小

| 检查项 | 上限 |
| --- | ---: |
| 上传 ZIP | 50 MB |
| GitHub 仓库 | 100 MB |
| 解压后的项目 | 100 MB |
| 文件数 | 10,000 个；统计时不计 `.git` 和 `.env` |

## 大型工具和依赖

不必把大型工具或依赖预先放进项目包；可以在 `observer.project.json` 的 `build` 步骤中联网下载或安装。可参考：

- [TypeScript 示例清单](../examples/typescript-pro/observer.project.json)：在构建阶段安装 npm 依赖并编译，npm 缓存位于 `/workspace`。
- [Rust 示例清单](../examples/rust-pro/observer.project.json)：在构建阶段编译，Cargo home 位于 `/tmp`。

## 提交前核对

- ZIP、仓库、解压后目录大小和文件数均未超过上限。
- 构建过程能在 10 分钟内完成，且不需要向只读根目录写入。
- 构建缓存和临时文件放在 `/workspace` 或 `/tmp`，并留意 `/tmp` 的 256 MB 上限。
- 需要的大型工具或依赖通过 `observer.project.json` 的 `build` 步骤安装。
