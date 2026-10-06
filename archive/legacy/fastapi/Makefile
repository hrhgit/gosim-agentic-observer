.PHONY: install dev test e2e seed admin docker
install:
	python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/playwright install chromium
dev:
	.venv/bin/uvicorn app.main:app --reload --port 8000
test:
	.venv/bin/pytest tests/test_scoring.py tests/test_web_flow.py
e2e:
	.venv/bin/pytest tests/e2e
admin:
	.venv/bin/python -m app.cli create-admin $(EMAIL)
docker:
	docker compose up -d --build
