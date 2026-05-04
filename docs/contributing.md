# Contributing

## Dev setup

```bash
git clone https://github.com/dvdthecoder/agent-container
cd agent-container
pip install -e ".[dev]"
modal token new   # saves to ~/.modal.toml
pre-commit install
```

---

## Running the project

### Smoke test (fastest, real end-to-end)

Runs against the [fixture repo](https://github.com/dvdthecoder/agent-container-fixture).
Requires Modal tokens and a deployed model endpoint.

```bash
make example                    # aider backend (default)
make example BACKEND=opencode   # opencode backend
```

Generates a unique task ID per run (`run-<hex6>`) so there is always real work to do.

### Full agent run against any repo

```bash
agent-run run \
  --repo https://github.com/org/myapp \
  --task "Fix the off-by-one in paginate()" \
  --backend aider \
  --timeout 600
```

### Dashboard

```bash
make dashboard
# → http://localhost:8000
```

Live view of running, completed, and failed runs — phases, log stream, PR links.

### MCP server

```bash
make mcp
# Starts stdio MCP server — wire up in .claude/settings.json
```

### Clean up stray containers

If a run is interrupted, Modal containers may keep running. Stop them:

```bash
make stop-sandboxes
```

---

## Running tests

```bash
make test               # unit tests — no external services, always fast
make test-integration   # real Modal sandbox, stub agent (no model needed)
make test-e2e           # real Modal sandbox + real model (nightly only)
make test-serve         # inference endpoint reachability (requires OPENAI_BASE_URL + OPENCODE_MODEL)
make test-analysis      # token/cost/quality analysis — fires real runs, creates PRs, prints Markdown table
```

`test-analysis` accepts optional overrides:

```bash
make test-analysis BACKENDS=aider,opencode RUNS=3 COST_PER_1M=0.80
make test-analysis > docs/analysis/$(date +%Y-%m-%d).md
```

Or run pytest directly with filters:

```bash
pytest tests/unit/ -v
pytest tests/unit/test_config.py -v          # single file
pytest tests/unit/ -k "test_clone" -v        # by name pattern
pytest tests/integration/ -v -m integration  # integration only
```

### Test pyramid

| Layer | What it tests | External services | When it runs |
|---|---|---|---|
| Unit | Config, spec, result, sandbox, git ops, proxy, CLI (all mocked) | None | Every commit |
| Integration | Full Modal sandbox lifecycle, stub agent | Modal | Every PR |
| E2e | Real model, fixture repo, full PR creation | Modal + model endpoint | Nightly |

**Unit tests mock everything external.** The Modal SDK and subprocess are patched. Add unit
tests first when adding a feature. 271 tests run in < 2 seconds.

The proxy test suite (`tests/unit/test_responses_proxy.py`) covers the Responses API →
Chat Completions conversion layer, including the SSE event sequence, tool conversion,
message reshaping, and token accumulation that underpins every opencode run.

**Integration tests use `--backend stub`.** The stub backend echoes the task and exits 0 — no
model tokens spent, no code changed. Exercises the full sandbox lifecycle at near-zero cost.
Skipped in CI if `MODAL_TOKEN_ID` secret is not set.

**E2e tests are nightly only.** Never run on every commit.

---

## Linting

```bash
make lint        # ruff check
ruff format .    # auto-format
```

All checks run automatically on every commit via pre-commit hooks.

---

## Docs

```bash
pip install mkdocs-material
mkdocs serve     # → http://localhost:8000
```

Published to GitHub Pages automatically on every push to `main`.

---

## Model deployment

### Deploy for development (vLLM, A100 80GB, ~$4/hr)

```bash
modal deploy modal/serve.py
```

### Deploy for production (vLLM, 2× A100 80GB)

```bash
SERVE_PROFILE=prod modal deploy modal/serve.py
```

### Phase 3 — SGLang validation

Deploys to a separate Modal app so the vLLM endpoint is untouched:

```bash
SERVE_PROFILE=experiment modal deploy modal/serve.py
# → https://your-org--agent-container-serve-experiment-serve.modal.run
```

Point at the SGLang endpoint and run the opencode smoke test:

```bash
OPENAI_BASE_URL=https://your-org--agent-container-serve-experiment-serve.modal.run \
  make example BACKEND=opencode
```

Exit criteria: non-empty diff + PR opened. If it fails, the failure is in SGLang's tool-call
parser — the opencode proxy is proven clean by Phase 2.

---

## Adding an agent backend

1. Create `agent/backends/your_backend.py`
2. Implement the `AgentBackend` protocol in `agent/backends/__init__.py`
3. Add the backend name to `_BACKENDS` in `agent/cli.py`
4. Add env var mapping in `SandboxConfig.env_for_backend()` in `sandbox/config.py`
5. Add unit tests in `tests/unit/test_backends.py`
6. Document in `docs/agents.md`

## Adding a model profile

1. Add the profile block to `modal/serve.py` (copy an existing `elif SERVE_PROFILE == ...` block)
2. Set a unique `_APP_NAME` if the profile needs a separate Modal app (see `sglang` profile)
3. Document the profile in `docs/models.md`

## Commit style

```
feat(sandbox): add ModalSandbox lifecycle
fix(cli): rewrite localhost to host.docker.internal
docs: update model provider options
chore(ci): bump actions/checkout to v6
```
