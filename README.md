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
│  agent-run CLI  /  Dashboard (localhost:8080)            │
│  Python API     /  MCP server (Claude Code, Gemini CLI)  │
└──────────────────────┬───────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────┐
│  ModalSandbox — ephemeral container per agent run        │
│  boot → clone → run agent → diff → PR → destroy         │
│  Destroy runs in finally — container never left dangling │
└──────────────────────┬───────────────────────────────────┘
                       │  modal.Sandbox (Python SDK)
                       ▼
┌──────────────────────────────────────────────────────────┐
│  Modal — container compute                               │
│  Each run gets a fresh ephemeral container               │
│  Coding agent: opencode / claude CLI / gemini CLI        │
│                                                          │
│  Calls → Modal model endpoint (internal network)         │
└──────────────────────┬───────────────────────────────────┘
                       │  Modal internal network
                       ▼
┌──────────────────────────────────────────────────────────┐
│  Modal GPU — SGLang inference server                     │
│  modal deploy modal/serve.py                             │
│  Qwen3-Coder on A100 · RadixAttention · scale-to-zero   │
└──────────────────────────────────────────────────────────┘
```

Everything runs on Modal. Sandbox compute and model serving are both Modal resources communicating
over Modal's internal network.

---

## Quickstart

### 1. Install

```bash
pip install agent-container
```

### 2. Configure

```bash
cp .env.example .env
```

Required:
```bash
# Modal — sandbox compute
MODAL_TOKEN_ID=...
MODAL_TOKEN_SECRET=...

# Git provider
GITHUB_TOKEN=ghp_...

# Model endpoint — pick one (see Model Setup below)
OPENAI_BASE_URL=https://api.together.xyz/v1
OPENAI_API_KEY=your-together-key
OPENCODE_MODEL=Qwen/Qwen3-Coder-80B-Instruct
```

### 3. Run

```bash
agent-run \
  --repo https://github.com/org/myapp \
  --task "Fix the off-by-one error in pagination"
```

That's it. No Docker, no servers, no infrastructure setup.

### Common commands

```bash
make test               # run unit tests
make dashboard          # start live dashboard at http://localhost:8000
make mcp                # start MCP server (stdio) for Claude Code / Gemini CLI
make lint               # ruff check
```

---

## Model setup

Two options — both work with zero code changes:

**Option A — MiniMax M2.5 hosted API** (recommended, no GPU setup):
```bash
OPENAI_BASE_URL=https://api.minimax.io/v1
OPENAI_API_KEY=your-minimax-api-key   # platform.minimax.io → Account Management → API Keys
OPENCODE_MODEL=MiniMax-M2.5
```

**Option B — Self-hosted on Modal GPU** (full control, scale-to-zero):
```bash
# Qwen3-Coder (default)
modal deploy modal/serve.py

# MiniMax M2.5 on 8× A100 80GB
SERVE_PROFILE=minimax modal deploy modal/serve.py
```

MiniMax M2.5 is currently **#1 on SWE-bench** — the standard benchmark for real-repo code editing.
It uses a MoE architecture (~45B active params, 1M context). See [Model setup](docs/models.md) for
full profile table and GPU requirements.

---

## Agent backends

Three coding agents are supported. All produce the same `AgentTaskResult` — the pipeline
(PR creation, dashboard, MCP) is identical regardless of backend.

```bash
agent-run --backend opencode ...   # default — OpenCode via OPENAI_BASE_URL
agent-run --backend claude  ...    # Claude Code CLI — Anthropic API
agent-run --backend gemini  ...    # Gemini CLI — Google AI / Vertex AI
```

| Backend | What runs inside the sandbox | Model source |
|---|---|---|
| `opencode` | OpenCode CLI | Any via `OPENAI_BASE_URL` |
| `claude` | Claude Code CLI | Anthropic API |
| `gemini` | Gemini CLI | Google AI / Vertex AI |

---

## Dashboard

```bash
make dashboard
# → http://localhost:8000
```

Live view of all running, completed, and failed agent runs. Each run streams phase changes and log
output in real time via Server-Sent Events. No page refresh needed.

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

The sandbox exposes an MCP server so you can trigger runs directly from your editor session.

```json
// .claude/settings.json  (already checked in — fill in your tokens)
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
        "OPENAI_API_KEY": ""
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
prompt, no diff ever touches the public internet. Complete audit trail via MR descriptions (diff,
test results, original task prompt). Human approval required before merge.

---

## Testing

```bash
make test               # unit tests — no external services, always free
make test-integration   # Modal sandbox lifecycle with stub agent (no LLM)
make test-e2e           # nightly — real model against fixture repo
```

| Layer | What runs | Cost | Trigger |
|---|---|---|---|
| Unit | All modules fully mocked | $0 | Every commit |
| Integration | Real Modal sandbox, stub agent | Modal compute only | Every PR |
| E2E | Real Modal sandbox + real model | ~$0.05/run | Nightly |

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
