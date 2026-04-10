# Contributing

## Dev setup

```bash
git clone https://github.com/dvdthecoder/agent-container
cd agent-container
pip install -e ".[dev]"
pre-commit install
```

## Running tests

```bash
# unit tests only (no external services, always fast)
pytest tests/unit/ -v

# integration tests (requires Modal token)
pytest tests/integration/ -v

# all
pytest
```

## Test pyramid

| Layer | What it tests | External services | When it runs |
|---|---|---|---|
| Unit | Config, spec, result, sandbox (mocked) | None | Every commit |
| Integration | Full Modal lifecycle, stub agent | Modal | Every PR |
| E2e | Real model, fixture repo | Modal + model endpoint | Nightly |

**Unit tests mock everything external.** `subprocess.run` and the Modal SDK are patched. If you're
adding a new feature, add unit tests first.

**Integration tests use `AGENT_BACKEND=stub`.** The stub backend echoes the task and exits 0 — no
model tokens spent, no code changed. Exercises the full sandbox lifecycle at near-zero cost. Skipped
in CI if `MODAL_TOKEN_ID` secret is not set.

**E2e tests are nightly only.** Never add an e2e test that runs on every commit.

## Adding an agent backend

1. Create `agent/backends/your_backend.py`
2. Implement the `AgentBackend` protocol defined in `agent/backends/__init__.py`
3. Add your backend name to `_agent_command()` in `modal/sandbox.py`
4. Add tests in `tests/unit/`
5. Document in `docs/agents.md`

## Adding a model provider option

No code change needed — model providers are just `OPENAI_BASE_URL` values. Update `docs/models.md`
and `.env.example` with the new provider's details.

## Linting and formatting

```bash
ruff check .
ruff format .
bandit -r sandbox/ agent/ modal/ dashboard/ mcp/
```

All of the above run automatically on every commit via pre-commit hooks.

## Commit style

```
feat(sandbox): add ModalSandbox lifecycle
fix(cli): rewrite localhost to host.docker.internal
docs: update model provider options
chore(ci): bump actions/checkout to v6
```

## Docs

```bash
pip install mkdocs-material
mkdocs serve   # → http://localhost:8000
```

Docs are published to GitHub Pages automatically on every push to `main`.
