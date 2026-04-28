.PHONY: test test-integration test-e2e lint mcp dashboard example

# ── unit tests (no external services, always free) ──────────────────────────
test:
	python3 -m pytest tests/unit/ -v

# ── integration tests (requires MODAL_TOKEN_ID + MODAL_TOKEN_SECRET) ────────
test-integration:
	python3 -m pytest tests/integration/ -v --tb=short -m integration

# ── e2e tests (nightly — requires Modal tokens + live model endpoint) ────────
test-e2e:
	python3 -m pytest tests/integration/ tests/e2e/ -v --tb=short -m e2e

# ── linting ──────────────────────────────────────────────────────────────────
lint:
	python3 -m ruff check .

# ── quick manual smoke test against fixture repo ────────────────────────────
example:
	agent-run run \
		--repo https://github.com/dvdthecoder/agent-container-fixture \
		--task "add a hello world function to the codebase" \
		--backend opencode

# ── servers ──────────────────────────────────────────────────────────────────
mcp:
	python3 -m mcp_server.server --transport stdio

dashboard:
	python3 -m uvicorn dashboard.app:app --reload --port 8000
