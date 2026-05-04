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
modal deploy modal/serve.py                          # test — Qwen2.5-Coder 32B, A100 80GB (default)
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
make test-serve             # inference endpoint reachability check
make test-analysis          # token/cost/quality analysis across backends
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

## Documentation

Full docs at **[dvdthecoder.github.io/agent-container](https://dvdthecoder.github.io/agent-container)**

- [Quickstart](https://dvdthecoder.github.io/agent-container/quickstart)
- [Architecture](https://dvdthecoder.github.io/agent-container/architecture)
- [Model setup & profiles](https://dvdthecoder.github.io/agent-container/models)
- [Agent backends](https://dvdthecoder.github.io/agent-container/agents)
- [Dashboard](https://dvdthecoder.github.io/agent-container/dashboard)
- [MCP integration](https://dvdthecoder.github.io/agent-container/mcp)
- [Enterprise / GitLab](https://dvdthecoder.github.io/agent-container/enterprise)
- [Testing](https://dvdthecoder.github.io/agent-container/contributing)
- [Lessons learned](https://dvdthecoder.github.io/agent-container/lessons-learned)
