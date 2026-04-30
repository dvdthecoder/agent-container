.PHONY: test test-integration test-e2e lint mcp dashboard example stop-sandboxes

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
# Usage: make example                    (aider, unique task each run)
#        make example BACKEND=opencode
BACKEND  ?= aider
TASK_ID  := $(shell python3 -c "import uuid; print(uuid.uuid4().hex[:6])")

example:
	agent-run run \
		--repo https://github.com/dvdthecoder/agent-container-fixture \
		--task "update hello_world to return the string 'Hello, World! run-$(TASK_ID)'" \
		--backend $(BACKEND) \
		--timeout 600

# ── cleanup — stop any stray sandbox containers left by failed runs ──────────
stop-sandboxes:
	@echo "Stopping all active agent-container-sandbox containers..."
	@modal container list 2>/dev/null \
		| awk '/agent-container/ {print $$1}' \
		| grep '^ta-' \
		| xargs -r -I{} modal container stop {} \
		&& echo "Done." || echo "No containers to stop."

# ── servers ──────────────────────────────────────────────────────────────────
mcp:
	python3 -m mcp_server.server --transport stdio

dashboard:
	python3 -m uvicorn dashboard.app:app --reload --port 8000
