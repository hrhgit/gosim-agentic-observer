FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
RUN pip install --no-cache-dir torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir numpy==2.2.6 scipy==1.15.3 lightgbm==4.6.0 scikit-learn==1.6.1 joblib==1.4.2 PyYAML==6.0.2
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
CMD ["python", "-m", "research.observing_conditions.adaptation_runtime", "--output", "/results/linux-runtime.json"]
