.PHONY: test test-integration test-e2e test-serve test-analysis combine-analysis analysis-matrix deploy lint mcp dashboard example stop-sandboxes

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
MODEL_LABEL  ?=
ENDPOINT     ?=
OUTPUT_JSON  ?=

test-analysis:
	ANALYSIS_BACKENDS=$(BACKENDS) \
	ANALYSIS_RUNS=$(RUNS) \
	ANALYSIS_COST_PER_1M=$(COST_PER_1M) \
	ANALYSIS_NO_PR=$(NO_PR) \
	ANALYSIS_MODEL_LABEL=$(MODEL_LABEL) \
	ANALYSIS_ENDPOINT=$(ENDPOINT) \
	ANALYSIS_OUTPUT_JSON=$(OUTPUT_JSON) \
	python3 scripts/token_analysis.py

# ── model server deployment ───────────────────────────────────────────────────
# Deploy a model to Modal and wait until the endpoint is ready.
# App names always include the model slug — URLs are self-describing and
# multiple models can run simultaneously without overwriting each other.
#
# Usage:
#   make deploy                    # default model (qwen2.5-coder-32b)
#   make deploy MODEL=qwen3-30b    # specific model
#   make deploy MODEL=all          # deploy all analysis models in sequence
#   make deploy PROFILE=experiment # SGLang profile
#
# After each deploy, wait_for_serve.py polls GET /v1/models until 200 and
# writes the new URL to OPENAI_BASE_URL in .env automatically.
#
MODEL         ?= qwen2.5-coder-32b
PROFILE       ?= prod
WAIT_TIMEOUT  ?= 900
# Models deployed (and waited on) by MODEL=all.
_ANALYSIS_MODELS := qwen2.5-coder-32b qwen3-30b

deploy:
ifeq ($(MODEL),all)
	@$(foreach m,$(_ANALYSIS_MODELS), \
		echo "==> Deploying $(m) ..." && \
		SERVE_MODEL=$(m) SERVE_PROFILE=$(PROFILE) modal deploy modal/serve.py && \
		python3 scripts/wait_for_serve.py \
			--app-name agent-container-serve-$(subst .,-,$(m)) \
			--timeout $(WAIT_TIMEOUT) && \
	) true
else
	SERVE_MODEL=$(MODEL) SERVE_PROFILE=$(PROFILE) modal deploy modal/serve.py
	python3 scripts/wait_for_serve.py \
		--app-name agent-container-serve-$(subst .,-,$(MODEL)) \
		--timeout $(WAIT_TIMEOUT) \
		--update-env
endif

# ── full model × backend analysis matrix ─────────────────────────────────────
# Deploy all 4 analysis models as isolated apps, run both backends against
# each, then combine results into a dated Markdown page.
#
# Usage:
#   make analysis-matrix                 # deploy + run + combine
#   make analysis-matrix BACKENDS=aider  # aider only (faster)
#   make analysis-matrix DATE=2026-05-05 # explicit date in output filename
#
analysis-matrix:
	python3 scripts/run_matrix.py \
		--backends $(BACKENDS) \
		--runs $(RUNS) \
		--cost-per-1m $(COST_PER_1M) \
		--date $(DATE) \
		--wait-timeout $(WAIT_TIMEOUT)

# ── combine analysis sidecars into a matrix page ─────────────────────────────
# Merge all JSON sidecars in docs/analysis/data/ into one dated Markdown page.
#
# Usage:
#   make combine-analysis                          # reads all *.json in data/
#   make combine-analysis DATE=2026-05-05          # explicit date in filename
#
DATE          ?= $(shell date +%Y-%m-%d)
_SIDECAR_DIR  := docs/analysis/data

combine-analysis:
	@mkdir -p $(_SIDECAR_DIR)
	python3 scripts/combine_analysis.py $(_SIDECAR_DIR)/*.json \
		> docs/analysis/$(DATE).md
	@echo "[combine] wrote docs/analysis/$(DATE).md"

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
