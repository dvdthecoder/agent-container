# agent-container

A secure, ephemeral sandbox for autonomous coding agents. Give it a task and a repo — it boots a
fresh container on [Modal](https://modal.com), runs an AI coding agent inside it, opens a PR, and
destroys the container. Nothing persists. Nothing leaks. No Docker required on your machine.

---

## What it does

```
$ agent-run run \
    --repo https://github.com/org/myapp \
    --task "Fix the off-by-one error in pagination" \
    --backend aider

  [sandbox] phase=WARMING   inference endpoint ready  elapsed=94s
  [sandbox] phase=BOOTING   starting Modal sandbox...
  [sandbox] phase=CLONING   git clone https://github.com/org/myapp
  [sandbox] phase=RUNNING   [aider] writing changes...
  [sandbox] phase=TESTING   pytest — 12 passed
  [sandbox] phase=PR        opening pull request...
  [sandbox] container terminated

  Done in 148s
  PR: https://github.com/org/myapp/pull/42   +67 −3
```

The agent never touches your local machine. The sandbox boots, does the work, opens the PR, and
is destroyed — all on Modal.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  agent-run CLI  /  Dashboard (localhost:8000)                   │
│  Python API     /  MCP server (Claude Code, Gemini CLI)         │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Modal — agent sandbox (ephemeral container, one per run)       │
│                                                                 │
│  WARMING → BOOTING → CLONING → RUNNING → TESTING → PR          │
│                                                                 │
│  Backends:                                                      │
│    aider      — Chat Completions direct, whole-file edit format │
│    opencode   — Responses API proxy → Chat Completions adapter  │
│    claude     — Claude Code CLI (Anthropic API)                 │
│    gemini     — Gemini CLI (Google AI)                          │
└──────────────────────────────┬──────────────────────────────────┘
                               │  POST /v1/chat/completions
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Modal — model serving (modal/serve.py)                         │
│  vLLM + Qwen2.5-Coder / Qwen3-Coder / MiniMax M2.5             │
│  Scale-to-zero. Weights cached in Modal Volume.                 │
└─────────────────────────────────────────────────────────────────┘
```

**Everything runs on Modal.** The agent sandbox and the model endpoint are separate Modal resources.
The sandbox calls the inference server over the network. No external API keys required for
self-hosted backends.

### Pipeline phases

| Phase | What happens |
|---|---|
| WARMING | Polls `GET /v1/models` until the inference endpoint is ready (handles vLLM cold start) |
| BOOTING | Creates the Modal sandbox container |
| CLONING | `git clone --depth 1` of the target repo |
| RUNNING | Runs the coding agent backend; streams output to your terminal in real time |
| TESTING | Auto-detects and runs the project test suite (pytest / npm / cargo / go) |
| PR | Pushes a timestamped branch and opens a pull request via the provider REST API |

---

## Quickstart

### 1. Install

```bash
pip install -e .
modal token new   # browser prompt — saves to ~/.modal.toml
```

### 2. Deploy your model

```bash
modal deploy modal/serve.py                          # test — Qwen2.5-Coder 7B, A10G (default)
SERVE_PROFILE=prod    modal deploy modal/serve.py    # prod — Qwen3-Coder 80B, 2× A100 80GB
SERVE_PROFILE=prod SERVE_MODEL=minimax-m2.5 \
  modal deploy modal/serve.py                        # prod — MiniMax M2.5, 8× A100 80GB
SERVE_PROFILE=experiment modal deploy modal/serve.py # experiment — SGLang engine, A10G
```

Modal prints the endpoint URL after deploy:
```
Created web endpoint: https://your-org--agent-container-serve-serve.modal.run
```

### 3. Configure

```bash
cp .env.example .env
```

Fill in:

```bash
MODAL_TOKEN_ID=ak-...
MODAL_TOKEN_SECRET=as-...

HF_TOKEN=hf_...          # huggingface.co/settings/tokens — read access

# Paste the URL from step 2 — bare host, no /v1 suffix needed
OPENAI_BASE_URL=https://your-org--agent-container-serve-serve.modal.run
OPENAI_API_KEY=modal
OPENCODE_MODEL=qwen2.5-coder   # must match SERVED_MODEL_NAME in modal/serve.py

GITHUB_TOKEN=ghp_...   # Contents (read) + Pull Requests (read/write)
```

### 4. Run

```bash
# Smoke test against the fixture repo
make example                    # aider backend
make example BACKEND=opencode   # opencode backend

# Real repo
agent-run run \
  --repo https://github.com/org/myapp \
  --task "Fix the off-by-one error in pagination" \
  --backend aider
```

### Common commands

```bash
make example                # smoke test — unique task per run, always real work
make example BACKEND=opencode
make test                   # unit tests — no external services
make dashboard              # live dashboard at http://localhost:8000
make mcp                    # MCP server (stdio) for Claude Code / Gemini CLI
make lint                   # ruff check
make stop-sandboxes         # clean up any stray Modal containers
```

---

## Agent backends

| Backend | API called | Requirement |
|---|---|---|
| `aider` | `POST /v1/chat/completions` direct | `OPENAI_BASE_URL` pointing at vLLM |
| `opencode` | `POST /v1/responses` → proxy → Chat Completions | Same as aider |
| `claude` | Anthropic API | `ANTHROPIC_API_KEY` in `spec.env` |
| `gemini` | Google AI API | `GEMINI_API_KEY` in `spec.env` |

The `opencode` backend starts a thin in-process proxy inside the sandbox that translates the
OpenAI Responses API to Chat Completions. vLLM only speaks Chat Completions — the proxy is a
pure format adapter with no model-specific logic.

All backends produce the same `AgentTaskResult`. Dashboard, MCP, and PR creation are identical
regardless of backend.

### Per-backend configuration

`SandboxConfig.env_for_backend(backend)` is the single place that maps config values to the env
vars each backend needs, in the exact format it expects:

- **aider / opencode**: `OPENAI_BASE_URL` with `/v1` suffix guaranteed, `OPENAI_API_KEY`, `OPENCODE_MODEL`
- **claude / gemini**: no inference vars (inject `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` via `spec.env`)

---

## Model profiles

Three profiles in `modal/serve.py`, selected by `SERVE_PROFILE`:

| Profile | Engine | Model | GPU | Context | Best for |
|---|---|---|---|---|---|
| `test` (default) | vLLM | Qwen2.5-Coder 7B | A10G | 32k | Development, CI |
| `prod` | vLLM | Qwen3-Coder 80B (default) | 2× A100 80GB | 128k | Production PRs |
| `prod` + `SERVE_MODEL=minimax-m2.5` | vLLM | MiniMax M2.5 | 8× A100 80GB | 1M | Best quality / SWE-bench |
| `experiment` | SGLang | Qwen2.5-Coder 7B | A10G | 32k | SGLang evaluation |

`prod` selects the model via `SERVE_MODEL` (default `qwen3-coder`). `experiment` deploys to a
separate Modal app (`agent-container-serve-experiment`) so the vLLM endpoint is never disturbed.

Scale-to-zero is on by default — you only pay while runs are active. Model weights are cached in a
Modal Volume so cold starts after the first don't re-download weights.

---

## Fixture repo

[dvdthecoder/agent-container-fixture](https://github.com/dvdthecoder/agent-container-fixture) is
the canonical smoke-test target. It contains:

- `greet.py` — a `hello_world()` function the agent edits on each run
- `mathlib.py` + `test_mathlib.py` — a `sum_to_n()` function with intentional bugs and
  failing pytest tests; useful for testing whether the agent can fix a broken test suite

`make example` targets this repo with a unique task ID per run (`run-<hex>`) so the agent always
has real work to do even when the function already exists.

---

## Python API

```python
from sandbox import ModalSandbox, SandboxConfig
from sandbox.spec import AgentTaskSpec

config = SandboxConfig.from_env()

spec = AgentTaskSpec(
    repo="https://github.com/org/myapp",
    task="Fix the off-by-one error in pagination",
    base_branch="main",
    backend="aider",
    create_pr=True,
    run_tests=True,
)

result = ModalSandbox(config).run(spec)

print(result.success)     # True
print(result.pr_url)      # https://github.com/org/myapp/pull/42
print(result.diff_stat)   # +67 −3
print(result.tests)       # SuiteResult(passed=12, failed=0, ...)
```

---

## Dashboard

```bash
make dashboard
# → http://localhost:8000
```

Live view of all runs — CLI and dashboard-started — newest first. Each run is a collapsible row:
collapsed shows the phase indicator, repo, backend, and whether the run was started from the CLI
or the dashboard; expanded shows the full log stream inline.

The left sidebar has a **serve panel** (deploy the model server from the UI, choose profile and
model) and the new-run form. Both CLI and dashboard runs write to the same SQLite log at
`~/.agent-container/runs.db`.

---

## MCP integration

`.claude/settings.json` is checked in — fill in your tokens:

```json
{
  "mcpServers": {
    "agent-container": {
      "command": "python3",
      "args": ["-m", "mcp_server.server"],
      "env": {
        "MODAL_TOKEN_ID": "",
        "MODAL_TOKEN_SECRET": "",
        "GITHUB_TOKEN": "",
        "OPENAI_BASE_URL": "",
        "OPENAI_API_KEY": "modal"
      }
    }
  }
}
```

Tools exposed: `sandbox_run`, `sandbox_list`, `sandbox_status`, `sandbox_stop`.

---

## Enterprise / GitLab

Swap `GITHUB_TOKEN` for `GITLAB_TOKEN`. PRs become Merge Requests. Everything else is identical.

```bash
GITLAB_TOKEN=glpat-...
GITLAB_URL=https://gitlab.yourcompany.com   # omit for gitlab.com
```

---

## Testing

```bash
make test               # unit tests — no external services, always free
make test-integration   # real Modal sandbox, stub agent (no LLM needed)
make test-e2e           # real Modal sandbox + real model
```

---

## Build phases

| Phase | Scope | Status |
|---|---|---|
| Phase 1 | vLLM + aider: direct Chat Completions, end-to-end PR | ✅ |
| Phase 2 | Clean opencode proxy: pure Responses API ↔ Chat Completions adapter | ✅ |
| Phase 3 | SGLang validation: deploy alongside vLLM, confirm tool calling end-to-end | ✅ |

---

## Challenges solved

This section documents the hard problems encountered during Phase 1 and Phase 2, and the
engineering decisions that resolved them.

### 1. vLLM cold start — 404s during boot

**Problem:** vLLM on an A10G takes ~75s for CUDA graph compilation. During this window, Modal
returns 404 for all routes including `/v1/chat/completions`. The agent would start, hit 404s, and
either silently fail or loop until timeout.

**Fix:** Added a WARMING phase that polls `GET /v1/models` before the sandbox is even created.
The run doesn't proceed to BOOTING until the inference endpoint confirms it is ready. WARMING is
the first phase — no container is wasted on a cold server.

---

### 2. aider's `--openai-api-base` flag mangles the URL

**Problem:** aider has an `--openai-api-base` flag for custom endpoints. When set, aider calls
`os.environ["OPENAI_API_BASE"] = value`. litellm reads `OPENAI_API_BASE` and strips the `/v1`
suffix before building the request URL, resulting in requests to `/chat/completions` instead of
`/v1/chat/completions` — a 404 on every model call.

**Fix:** Removed `--openai-api-base` from the aider command entirely. `OPENAI_BASE_URL` (note:
`BASE`, not `API_BASE`) is read correctly by the OpenAI SDK without path mangling. The `/v1`
suffix is guaranteed by `SandboxConfig.env_for_backend("aider")` before the container starts.

---

### 3. Config provider owns per-backend env vars

**Problem:** Each backend has different expectations for env var names and URL format. aider needs
`OPENAI_BASE_URL` with `/v1`. opencode needs the same. claude and gemini don't use these vars at
all. URL normalisation was scattered — in runner scripts, in config, inconsistently.

**Fix:** `SandboxConfig.env_for_backend(backend)` is the single source of truth. It maps config
values to the exact env vars each backend needs, in the format it expects. Runner scripts read
env vars verbatim — no normalisation at runtime. Adding a new inference engine means changing one
method.

---

### 4. Sandbox not terminating — `terminate()` is fire-and-forget

**Problem:** `modal.Sandbox.terminate()` defaults to `wait=False`. It dispatches the kill signal
and returns immediately. The container kept running in the Modal dashboard after the CLI exited —
visible as "still running" long after the job completed.

**Fix:** Changed to `sb.terminate(wait=True)`. The call now blocks until Modal confirms the
container has stopped. "[sandbox] container terminated" in the logs means the container is
actually gone.

---

### 5. Hanging CLI after agent timeout

**Problem:** When `run_agent` hit its timeout, it called `sb.terminate()` then returned
`(output, exit_code=1)`. `sandbox.py` then called `collect_diff(sb, ...)` — `sb.exec()` on a
terminated Modal container blocks indefinitely. The CLI appeared frozen and required manual
killing.

**Fix:** `run_agent` now raises `TimeoutError` in the timeout path instead of returning. This
unwinds through `sandbox.py`'s inner `except Exception` block, which skips `collect_diff`,
terminates the sandbox (no-op — already done), and returns a failure result immediately.

---

### 6. Model asking clarifying questions instead of writing code

**Problem:** `--map-tokens 0` was used to disable the aider repo map and avoid a multi-minute
scan on fresh clones. Without any repo context, the model responded with clarifying questions
("where should I add this function?") instead of editing files — resulting in an empty diff and a
false failure.

**Fix:** Changed to `--map-tokens 1024`. This gives the model a concise file list and function
signature summary (a few seconds on small-to-medium repos) without the expensive full scan. The
model can now pick a sensible target file and proceed without asking.

---

### 7. `__pycache__` polluting PR diffs

**Problem:** The TESTING phase runs pytest, which compiles `.py` files to `__pycache__/*.pyc`.
These binary files were not gitignored in the target repo, so they appeared in `collect_diff`
as the only changes — masking whether the agent wrote any real code. PR #3 from the opencode
smoke test contained only `.pyc` file changes.

**Fix:** After cloning, `git_ops.clone()` writes common build artifact patterns to
`.git/info/exclude` — a local-only file that is never committed. This keeps the diff clean without
touching the target repo's `.gitignore`.

---

### 8. opencode calls Responses API — vLLM doesn't implement it

**Problem:** opencode v1.14+ calls `POST /v1/responses` (OpenAI Responses API). No self-hosted
inference server (vLLM, SGLang) implements this endpoint. Every opencode request returned 404.

**Fix:** `opencode_runner.py` starts a thin in-process HTTP proxy on `localhost:8080` that
intercepts `/v1/responses` and translates to Chat Completions: converts input items to messages,
maps `tool_result` to `role:tool`, maps `function_call` to assistant messages with `tool_calls`,
passes tools in the standard `tools` field, and translates the response back. opencode is
configured to point at the proxy via `~/.config/opencode/config.json`. The proxy is a pure
format adapter — no model-specific logic.

---

### 9. SGLang v0.4.7 tool-calling crashes — and Phase 3 validation

**Problem:** The original inference server was SGLang v0.4.7. It had multiple blocking bugs:
`--enable-auto-tool-choice` did not exist, `--tool-call-parser qwen25` crashed the server
process on the first request with tool schemas, and streaming with tools hung indefinitely.
The original opencode proxy worked around all of these with Qwen-native text injection and
text-level `<tool_call>` parsing — 389 lines of model-specific glue code.

**Phase 1 fix:** Switched the primary inference server to vLLM. vLLM has a stable, first-class
OpenAI-compatible API with `--enable-auto-tool-choice` and `--tool-call-parser`. All SGLang
workarounds were removed from `opencode_runner.py` in Phase 2 (170 lines added, 389 removed).
The proxy became a clean format adapter with no model-specific code.

**Phase 3 — SGLang re-validation:** After the proxy was clean, SGLang was re-tested in isolation
against the same model (Qwen2.5-Coder 7B, A10G) to determine whether newer versions had fixed
the tool-calling bugs. Key findings:

- `--tool-call-parser qwen` and `qwen25` still hang on the first request with tool schemas
  (0 chunks received, server becomes unresponsive)
- `--tool-call-parser hermes` resolves correctly — first tool call with 10 tools returned in
  3 seconds, full run (WARMING → PR) completed in 29 seconds
- SGLang requires a CUDA devel base image (`nvidia/cuda:12.4.1-devel-ubuntu22.04`) and
  `libnuma1` — it JIT-compiles rope/attention kernels at model-load time, which fails in a
  bare debian_slim container

**Conclusion:** SGLang is viable with the `hermes` parser. vLLM remains the default because it
works out-of-the-box with no image surgery. Both run simultaneously as separate Modal apps —
`agent-container-serve` (vLLM) and `agent-container-serve-experiment` (SGLang).

---

## Documentation

Full documentation at **[dvdthecoder.github.io/agent-container](https://dvdthecoder.github.io/agent-container)**

- [Architecture](https://dvdthecoder.github.io/agent-container/architecture)
- [Quickstart](https://dvdthecoder.github.io/agent-container/quickstart)
- [Model setup](https://dvdthecoder.github.io/agent-container/models)
- [Enterprise / GitLab](https://dvdthecoder.github.io/agent-container/enterprise)
- [MCP integration](https://dvdthecoder.github.io/agent-container/mcp)
- [Contributing](https://dvdthecoder.github.io/agent-container/contributing)
