# 赛季时间分配门槛实验

先比较现有基线与知道整季自行生成天气的排程分配；通过差异筛查后，测“喂答案”的收益。达到引导门槛后才进入学习模型阶段。本工具不把自行生成天气或快速模拟成绩视为线上预测。

```powershell
research/blockage/.venv/Scripts/python.exe -m research.allocation.run_gates --out artifacts/allocation-smoke-v2 --smoke
research/blockage/.venv/Scripts/python.exe -m research.allocation.run_gates --out artifacts/allocation-v4 --jobs 4
```

先冻结来源散列、两套新天气种子、模型/引导参数和门槛。`--jobs` 可选择 1/2/4 个独立单线程模拟进程；只有 A/C 两个排程任务，因此排程始终至多两个并行，每个需预留至少 1 GB 内存。`--reuse-screening` 可读取先前中断编排中已经完整产出的排程或基线，要求所有科学代码与输入散列相同；被中断的部分模拟不复用。新协议记录旧协议与复用文件的散列。

`upper_bound.py --export` 改用规划器的 `Grid` 和目标首次出现的天区编号，中心取同样的球面向量平均；导出分配矩阵 `x[z,b]`、时间段起止/开放秒数、天区中心/键、累计质量秒与原始对偶值。目标函数是最小化负得分，因此对偶值取负才是得分边际价值。`--max-bins` 只供小规模流程检查。

`compare_allocation.py` 联合 `decisions.csv` 和按光纤存储的 `observations.csv` 计算曝光时长，不重复累计一曝光的多条光纤记录。两个分配均按同样的 5 度 `Grid` 汇总，输出每分区数值及半 L1 距离。如果 A、C 两卡均小于 10%，结束；至少一卡达到 10% 才继续完整 A/C 引导比较。

研究工具只修改实验目录里的临时基线副本：`BASE_GUIDE` 指向分配文件；软引导系数为 `1 + G*x/max(x[:,b])`，G 固定为 1/3，只乘科学潜力打分项，不改变必观测和请求的奖励项；硬引导排除 x<0.02 的普通天区，保留必观测与当前请求天区。无引导时倍率严格为 1，原参赛规划器不变。

三套天气为现有 `formal-v4-self-generated-v2` 以及种子 260031/260032 的新生成集合。新集合通过已有 `make_holdout_set.py` 生成，位于被忽略的 `agents/claude-baseline-v1/eval_sets/`。每套 A/C × 基线/软1/软3/硬共八次完整赛季模拟。仅在第一套天气选出 A 提升最大的、C 不退步超过 1% 的引导；验证仅使用后两套，A 平均须至少 +3%，C 平均不低于 -1%。

`report.json` 保留各场得分、成对增益、第一套选择和验证均值。门槛失败时不生成 3.2/3.3 的训练场景或模型，不移植到 TypeScript，也不作平台提交。只有实际通过后才补齐后续阶段实现。
