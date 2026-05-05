.PHONY: test test-integration test-e2e test-serve test-analysis deploy lint mcp dashboard example stop-sandboxes

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

# ── model server deployment ───────────────────────────────────────────────────
# Deploy a model to Modal and wait until the endpoint is ready.
#
# Usage:
#   make deploy                             # replace prod with default model
#   make deploy MODEL=qwen3-8b              # replace prod with qwen3-8b
#   make deploy MODEL=qwen3-8b ISOLATED=1   # isolated app (keeps prod running)
#   make deploy MODEL=all                   # deploy all analysis models as
#                                           # isolated apps simultaneously
#   make deploy MODEL=x PROFILE=experiment  # SGLang profile
#
# ISOLATED=1: app name becomes agent-container-serve-{model} so multiple
# models can run in parallel without overwriting each other.  MODEL=all
# always uses isolated apps.
#
# After each deploy, wait_for_serve.py polls GET /v1/models until 200 so
# the next make test-analysis call hits a warm endpoint immediately.
#
MODEL         ?= qwen2.5-coder-32b
PROFILE       ?= prod
ISOLATED      ?= 0
WAIT_TIMEOUT  ?= 900
# Models deployed (and waited on) by MODEL=all.
_ANALYSIS_MODELS := qwen3-8b qwen2.5-coder-32b

deploy:
ifeq ($(MODEL),all)
	@$(foreach m,$(_ANALYSIS_MODELS), \
		echo "==> Deploying $(m) (isolated) ..." && \
		SERVE_MODEL=$(m) SERVE_PROFILE=$(PROFILE) SERVE_ISOLATED=1 modal deploy modal/serve.py && \
		python3 scripts/wait_for_serve.py \
			--app-name agent-container-serve-$(subst .,-,$(m)) \
			--timeout $(WAIT_TIMEOUT) && \
	) true
else ifeq ($(ISOLATED),1)
	SERVE_MODEL=$(MODEL) SERVE_PROFILE=$(PROFILE) SERVE_ISOLATED=1 modal deploy modal/serve.py
	python3 scripts/wait_for_serve.py \
		--app-name agent-container-serve-$(subst .,-,$(MODEL)) \
		--timeout $(WAIT_TIMEOUT)
else
	SERVE_MODEL=$(MODEL) SERVE_PROFILE=$(PROFILE) modal deploy modal/serve.py
	python3 scripts/wait_for_serve.py --timeout $(WAIT_TIMEOUT)
endif

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
