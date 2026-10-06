# 曝光全零概率实验

使用收到的正式比赛反馈与公开输入，预测一次候选曝光全零的概率。研究工具不读取隐藏天气，也不排序或删除候选。先运行小规模流程检查，再运行固定参数的分组验证；未通过预定门槛不导出或接入参赛智能体。

## 运行

从项目根目录执行。Windows 示例使用隔离环境；可另用 Python 3.11–3.12 安装 `pyproject.toml` 中的依赖。训练仅用 CPU，每个进程限一个计算线程。

```powershell
uv venv --python python --system-site-packages research/blockage/.venv
uv pip install --python research/blockage/.venv/Scripts/python.exe lightgbm
research/blockage/.venv/Scripts/python.exe -m pytest research/blockage/tests research/allocation/tests -q
research/blockage/.venv/Scripts/python.exe -m research.blockage.dataset --materials gosim-training-bundle --out artifacts/blockage-v1
research/blockage/.venv/Scripts/python.exe -m research.blockage.train --dataset artifacts/blockage-v1/exposures.parquet --out artifacts/blockage-v2/train --jobs 2
research/blockage/.venv/Scripts/python.exe -m research.blockage.transfer --dataset artifacts/blockage-v1/exposures.parquet --out artifacts/blockage-v2/old-only
```

全新环境应安装全部依赖；上述系统环境复用命令适用于本机已安装 NumPy、Pandas、PyArrow、PyTorch、scikit-learn 的情况。各实验的 `protocol.json` 保存实际软件版本。相同目录的协议发生变化时会拒绝覆盖，应使用新目录。

## 信息与划分

- 样本开始/结束取相邻决策请求时间；只保留有命中结果且间隔在 `(0, 4000]` 秒内的曝光。
- 基线的指向取命中目标的球面平均、按曝光中点换算水平坐标。这是近似指向，记录 `direction_inferred`；样本仍有“有命中”筛选与策略选择偏差。
- 探测方向取前一动作的 `pointing`。动作数量或类型不匹配时直接失败，不用命中目标方向掩盖对齐问题。
- 先生成曝光输入，后摄入结果所在请求的新公告。反馈历史只来自同一运行。交接抽取必须同时满足公告发布和该运行实际收到请求的时刻严格早于开始。
- 各种公告、预报、地形、历史反馈以及交接证据保留缺失标记。抽取中的 `other`、低置信度和 `degraded` 状态原样保留，不把未分类关闭解释成停电。
- `avoidable_hint`、当前 `zero`、连续全零首条标记只作标签或评测字段，不放进模型输入。
- 外层留出 A1/B1/C1/D1 的其中一卡，其基线与探测同时留出。其余新卡与全部旧卡训练。内层逐一留出训练卡，将全部内层留卡预测合并用于保序校准与阈值选择；最终训练预算取内层最佳预算的中位数。
- 树、GRU、逻辑回归共享当前特征与最近 16 次曝光的特征/已收到结果；树与逻辑回归将同一顺序历史展开。GRU 隐藏层 32、输出头 32，不作参数搜索。
- 旧卡迁移检查去掉全部交接特征。使用同一外层测试卡，但仅旧卡训练和内层验证。

## 指标与判定

每卡报告全部 10 个概率档的样本数、加权预测/实际概率与偏差；不少于 50 个样本的每档都须偏差不超过 0.05。卡内基线按时长、探测按次数；两个来源各占一半总权重，跨卡各占相同权重。另报各来源的指标，避免把时长与次数直接相加。

净节省按基线曝光时间计算；探测次数没有可直接解释的赛季小时数。阈值只在内层预测上选择。`avoidable_hint` 仅说明前后存在得分曝光，不能保证另一个指向在当前时刻可行，故净节省是离线代理指标。

至少三卡同时满足可信度、Brier 不高于 R2 的 0.9 倍、净节省不少于 1% 才进入闭环。至少三卡仅满足前两项，则只考虑概率替换。两种条件均未达到就结束此方向。初轮 `--inner-mode fixed` 留作探索记录，正式判定用完整的 `card-oof`。

R2 为按事件组合、夹角档和高度档的训练数据查表，未见表项回退到训练先验。`R2_hand` 复现 `closed_probability` 的 1/0.85/0.35 及天顶折半，仅是该风关闭辅助函数的数值参照，不能冒充全智能体的零分预测；这些手定数值曾参考同批正式运行，亦不作为独立训练证据。

产物包含 `exposures.parquet`、数据散列与统计、每折预测、`folds.json`、`predictions.parquet`、`report.json` 和失败记录。门槛失败时保留研究代码和证据，不写参赛运行入口。
