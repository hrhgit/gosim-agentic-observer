FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY challenge ./challenge
COPY worker ./worker
RUN useradd -r -u 1001 sac && mkdir -p /srv/data && chown -R sac:sac /srv
USER sac
# participant packages may install requirements.txt into a per-run virtualenv; pip is available
CMD ["python", "-m", "worker.main", "run"]
