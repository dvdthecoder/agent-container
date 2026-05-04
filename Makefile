.PHONY: test test-integration test-e2e test-serve test-analysis lint mcp dashboard example stop-sandboxes

# ── unit tests (no external services, always free) ──────────────────────────
test:
	python3 -m pytest tests/unit/ -v

# ── integration tests (requires MODAL_TOKEN_ID + MODAL_TOKEN_SECRET) ────────
test-integration:
	python3 -m pytest tests/integration/ -v --tb=short -m integration

# ── e2e tests (nightly — requires Modal tokens + live model endpoint) ────────
test-e2e:
	python3 -m pytest tests/integration/ tests/e2e/ -v --tb=short -m e2e

# ── serve endpoint tests (requires OPENAI_BASE_URL + OPENCODE_MODEL) ─────────
test-serve:
	python3 -m pytest tests/integration/test_serve_reachable.py -v --tb=short -m serve

# ── token / cost / quality analysis across backends ──────────────────────────
# Fires real runs against the fixture repo, measures tokens + cost, prints a
# Markdown summary.  Requires OPENAI_BASE_URL and OPENCODE_MODEL to be set.
#
# Usage:
#   make test-analysis                           # aider + opencode, 1 run each
#   make test-analysis BACKENDS=opencode RUNS=3  # 3 opencode runs
#   make test-analysis BACKENDS=aider COST_PER_1M=0.80
#   make test-analysis > docs/analysis/$(date +%Y-%m-%d).md
#
BACKENDS     ?= aider,opencode
RUNS         ?= 1
COST_PER_1M  ?= 1.00
NO_PR        ?= 0

test-analysis:
	ANALYSIS_BACKENDS=$(BACKENDS) \
	ANALYSIS_RUNS=$(RUNS) \
	ANALYSIS_COST_PER_1M=$(COST_PER_1M) \
	ANALYSIS_NO_PR=$(NO_PR) \
	python3 scripts/token_analysis.py

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
		--task "in greet.py, update the hello_world function to return the string 'Hello, World! run-$(TASK_ID)'" \
		--backend $(BACKEND) \
		--timeout 600

# ── cleanup — stop any stray sandbox containers left by failed runs ──────────
stop-sandboxes:
	@echo "Stopping all active agent-container-sandbox containers..."
	@modal container list 2>/dev/null \
		| awk '/agent-container-sandbox/ {print $$1}' \
		| grep '^ta-' \
		| xargs -r -I{} modal container stop {} \
		&& echo "Done." || echo "No containers to stop."

# ── servers ──────────────────────────────────────────────────────────────────
mcp:
	python3 -m mcp_server.server --transport stdio

dashboard:
	python3 -m uvicorn dashboard.app:app --reload --port 8000
