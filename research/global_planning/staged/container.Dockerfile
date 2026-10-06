FROM --platform=linux/amd64 node:22-slim
WORKDIR /workspace
COPY agents/global-planner-v2/package.json agents/global-planner-v2/package-lock.json ./
COPY agents/global-planner-v2/runtime ./runtime
COPY agents/global-planner-v2/src ./src
COPY agents/global-planner-v2/models ./models
COPY agents/global-planner-v2/tsconfig.json ./
ENV NPM_CONFIG_CACHE=/workspace/.npm-cache PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
RUN node runtime/setup.cjs && npm ci --include=dev && npm run build
