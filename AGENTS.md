# AGENTS.md — agent-container

Conventions and rules for coding agents working in this repository.

## Architecture boundaries

```
agent/          Sandbox-side code. No Modal imports allowed.
modal/          Deployment code. No agent logic allowed.
sandbox/        Shared dataclasses (AgentTaskSpec, AgentTaskResult, SandboxConfig).
                No inference-layer or Modal imports.
dashboard/      FastAPI UI. Reads from SQLite via RunStore only.
mcp_server/     MCP server. Calls ModalSandbox — no direct agent logic.
scripts/        Analysis and deployment helpers. Standalone scripts only.
```

Cross-boundary imports are caught by `scripts/check_container_imports.py` in CI.

## Coding conventions

- Type annotations on all public functions and methods
- Dataclasses for value objects — not plain dicts
- No `shell=True` in subprocess calls — always use list form
- No new external dependencies without discussion
- No model-specific logic in `agent/opencode_runner.py` — the proxy is a pure format adapter

## Testing

```bash
# Unit tests — no network, no Modal required
python3 -m pytest tests/unit/ -q

# Lint + format (must pass CI)
ruff check .
ruff format --check .
```

- Unit tests live in `tests/unit/` — no network, no Modal, no filesystem side effects
- Integration tests live in `tests/integration/` — require Modal credentials and a live endpoint
- New functions in `agent/` need unit tests; new scripts in `scripts/` do not

## Security

- Never hardcode credentials, API keys, or tokens — use environment variables
- Never use `eval()` or `exec()`
- Never use `shell=True` in subprocess calls
- Input validation at system boundaries: CLI args (`agent/cli.py`), dashboard API (`dashboard/router.py`), MCP tool inputs (`mcp_server/server.py`)

## Before committing

```bash
ruff check . && ruff format --check . && python3 -m pytest tests/unit/ -q
```
