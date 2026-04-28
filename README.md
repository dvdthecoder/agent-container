# agent-container

A secure, ephemeral sandbox for autonomous coding agents. Give it a task and a repo — it boots a
fresh container on [Modal](https://modal.com), runs an AI coding agent inside it, opens a PR, and
destroys the container. Nothing persists. Nothing leaks. No Docker required on your machine.

---

## What it does

```
$ agent-run \
    --task "Add rate limiting to /api/login — max 5 requests/min per IP" \
    --repo https://github.com/org/myapp

  booting sandbox...        (Modal ephemeral container)
  cloning repo...
  running opencode...
  opening PR...

  ✓ Done in 2m 14s
  PR: https://github.com/org/myapp/pull/42   +67 −3
```

A Modal sandbox boots with the specified image, the agent clones the repo and makes the changes, a
PR is opened, and the sandbox is destroyed. The agent never touches your local machine.

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  agent-run CLI  /  Dashboard (localhost:8000)            │
│  Python API     /  MCP server (Claude Code, Gemini CLI)  │
└──────────────────────┬───────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────┐
│  Modal — agent sandbox (ephemeral container per run)     │
│  boot → clone → run agent → diff → PR → destroy         │
└──────────────────────┬───────────────────────────────────┘
                       │  Modal internal network
                       ▼
┌──────────────────────────────────────────────────────────┐
│  Modal — model serving (modal/serve.py)                  │
│  SGLang + Qwen3-Coder / MiniMax M2.5                    │
│  Scale-to-zero. Weights cached in Modal Volume.          │
└──────────────────────────────────────────────────────────┘
```

**Everything runs on Modal.** The agent sandbox and the model endpoint are both Modal resources
communicating over Modal's internal network. No external API keys required.

---

## Quickstart

```
 YOU                          MODAL
  │                             │
  │  1. pip install             │
  │     modal token new ───────▶│ authenticate
  │                             │
  │  2. modal deploy ──────────▶│ build image
  │     modal/serve.py          │ download weights (once)
  │                             │ start SGLang server
  │                             │◀─── prints endpoint URL
  │                             │
  │  3. cp .env.example .env    │
  │     paste URL + tokens      │
  │                             │
  │  4. make dashboard ─────────┼──▶ http://localhost:8000
  │     (or agent-run CLI)      │         │
  │                             │         │ submit task
  │                             │         ▼
  │                             │    boot sandbox
  │                             │    git clone repo
  │                             │    run opencode ──▶ SGLang (your model)
  │                             │    git diff
  │                             │    open PR
  │                             │    destroy sandbox
  │                             │◀─── PR URL + diff stat
  │◀────────────────────────────│
  ✓ Done
```

### 1. Install

```bash
pip install agent-container
modal token new   # browser prompt — saves to ~/.modal.toml
```

### 2. Deploy your model

```bash
modal deploy modal/serve.py                        # Qwen3-Coder 8B  — cheap, good for testing
SERVE_PROFILE=prod    modal deploy modal/serve.py  # Qwen3-Coder 80B — production quality
SERVE_PROFILE=minimax modal deploy modal/serve.py  # MiniMax M2.5    — best SWE-bench score
```

Modal prints the endpoint URL on deploy — you need it in the next step:
```
✓ Created web endpoint: https://your-org--agent-container-serve.modal.run
```

### 3. Configure

```bash
cp .env.example .env
```

Fill in four things:

```bash
MODAL_TOKEN_ID=ak-...
MODAL_TOKEN_SECRET=as-...

HF_TOKEN=hf_...          # huggingface.co/settings/tokens — read access

OPENAI_BASE_URL=https://your-org--agent-container-serve.modal.run/v1   # from step 2
OPENAI_API_KEY=modal
OPENCODE_MODEL=qwen3-coder   # or minimax-m2.5

GITHUB_TOKEN=ghp_...   # Contents (read) + Pull Requests (read/write)
```

### 4. Run

```bash
agent-run run \
  --repo https://github.com/org/myapp \
  --task "Fix the off-by-one error in pagination"
```

That's it. No Docker, no servers beyond Modal.

### Common commands

```bash
make example            # smoke test against fixture repo (full pipeline)
make test               # unit tests — no external services
make dashboard          # live dashboard at http://localhost:8000
make mcp                # MCP server (stdio) for Claude Code / Gemini CLI
make lint               # ruff check + format check
```

---

## Model profiles

Three GPU profiles in `modal/serve.py`:

| Profile | Model | GPU | Context | Best for |
|---|---|---|---|---|
| `test` (default) | Qwen3-Coder 8B | A10G | 32k | Development, CI |
| `prod` | Qwen3-Coder 80B | 2× A100 80GB | 128k | Production PRs |
| `minimax` | MiniMax M2.5 | 8× A100 80GB | 1M | Best quality |

Scale-to-zero is on by default — you only pay while runs are active. Model weights are cached in a
Modal Volume so cold starts after the first don't re-download.

See [Model setup docs](docs/models.md) for details on SGLang, RadixAttention caching, and GPU sizing.

---

## Agent backends

```bash
agent-run --backend opencode ...   # default — opencode via OPENAI_BASE_URL
agent-run --backend claude  ...    # Claude Code CLI — Anthropic API
agent-run --backend gemini  ...    # Gemini CLI — Google AI / Vertex AI
```

All backends produce the same `AgentTaskResult`. PR creation, dashboard, MCP are identical
regardless of backend. The `opencode` backend uses the self-hosted Modal endpoint by default.

---

## Dashboard

```bash
make dashboard
# → http://localhost:8000
```

Live view of all running, completed, and failed agent runs. Streams phase changes and agent output
in real time via Server-Sent Events.

```
● BOOTING     starting Modal sandbox...
● CLONING     git clone https://github.com/org/myapp
● RUNNING     [opencode] Reading api/login.py...
◉ DONE        PR #42 opened   +67 −3
```

---

## Python API

```python
from sandbox import ModalSandbox, SandboxConfig, AgentTaskSpec

config = SandboxConfig.from_env()

spec = AgentTaskSpec(
    repo="https://github.com/org/myapp",
    task="Add rate limiting to /api/login — max 5 req/min per IP",
    base_branch="main",
    create_pr=True,
)

result = ModalSandbox(config).run(spec)

print(result.pr_url)      # https://github.com/org/myapp/pull/42
print(result.diff_stat)   # +67 −3
```

---

## MCP integration (Claude Code / Gemini CLI)

`.claude/settings.json` is already checked in — fill in your tokens:

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

Then inside Claude Code:
```
"Fix the pagination bug in org/myapp"
→ Claude calls sandbox_run MCP tool
→ Modal sandbox boots, agent edits, PR opens
→ "Done. PR #42: +67 −3"
```

Tools exposed: `sandbox_run`, `sandbox_list`, `sandbox_status`, `sandbox_stop`.

---

## Enterprise / GitLab

Swap `GITHUB_TOKEN` for `GITLAB_TOKEN`. PRs become Merge Requests. Everything else is identical.

```bash
GITLAB_TOKEN=glpat-...
GITLAB_URL=https://gitlab.yourcompany.com   # omit for gitlab.com
```

**Full air-gap setup**: GitLab on-prem + Modal in a private VPC + SGLang on-prem = no code, no
prompt, no diff ever touches the public internet.

---

## Testing

```bash
make test               # unit tests — no external services, always free
make test-integration   # real Modal sandbox, stub agent (no LLM needed)
make test-e2e           # real Modal sandbox + real model
```

---

## Milestones

| Milestone | Scope | Status |
|---|---|---|
| M1: Core dataclasses | `SandboxConfig`, `AgentTaskSpec`, `AgentTaskResult` | ✅ |
| M2: Modal sandbox | `ModalSandbox` boot/run/teardown, CLI | ✅ |
| M3: Agent internals | Backends, runner, tester, git ops, GitHub/GitLab providers | ✅ |
| M4: Dashboard | FastAPI SSE API, live terminal UI | ✅ |
| M5: Model serving | `modal/serve.py` — SGLang + Qwen3-Coder on Modal GPU | ✅ |
| M6: MCP + backends | MCP server, Claude Code + Gemini CLI backends | ✅ |
| M7: Hardening | CI workflows, examples, docs | ✅ |

---

## Documentation

Full documentation at **[dvdthecoder.github.io/agent-container](https://dvdthecoder.github.io/agent-container)**

- [Architecture](https://dvdthecoder.github.io/agent-container/architecture)
- [Quickstart](https://dvdthecoder.github.io/agent-container/quickstart)
- [Model setup](https://dvdthecoder.github.io/agent-container/models)
- [Enterprise / GitLab](https://dvdthecoder.github.io/agent-container/enterprise)
- [MCP integration](https://dvdthecoder.github.io/agent-container/mcp)
- [Contributing](https://dvdthecoder.github.io/agent-container/contributing)
