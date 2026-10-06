# 线上 PyTorch 镜像

本提交使用下列公开、固定摘要的 Linux x86_64 镜像：

```text
herh27/gosim-llm-observer-pytorch@sha256:194597cdb41f3d766054b8f9fe7787534cb4f5cf95e212869d9f88a96e0a322c
```

可读标签是 `torch-2.8.0-node22-v1`。镜像包含 Node 22、Python 3.12.11、PyTorch 2.8.0+cpu、NumPy 2.2.6 和 SciPy 1.15.3，并设置 `BASE_MODEL_PYTHON=/opt/venv/bin/python`。模型权重仍在参赛 ZIP 内，不在镜像内。

这样做是因为 PyTorch 的 `libtorch_cpu.so` 大于平台每个新写文件的 256 MiB 限制。它预装在镜像只读层后，比赛容器只读取该文件，不会在构建或运行阶段创建它。

镜像构建来源是 `container/Dockerfile.pytorch`：固定 Node 基础镜像摘要，核对 uv 0.8.22 的 SHA-256，再固定 Python 和 Python 包版本。2026-10-06 已在与平台相同的 Linux x86_64、只读根目录、2 CPU、2 GB、`/tmp` 256 MiB 和单文件 256 MiB 限制下，完成 `npm ci`、`npm run build` 与真实 `conditions-reliability` 推理。该检查不替代官方公开测试或正式成绩。
