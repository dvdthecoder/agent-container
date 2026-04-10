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
│  Each run gets a fresh container                         │
│  Coding agent installed inside: opencode / claude /      │
│  gemini CLI (configurable per task)                      │
│                                                          │
│  Agent calls → OPENAI_BASE_URL  (pluggable, see below)  │
└──────────────────────┬───────────────────────────────────┘
                       │  HTTP  (any OpenAI-compatible endpoint)
                       ▼
┌──────────────────────────────────────────────────────────┐
│  Model endpoint — pick any:                              │
│                                                          │
│  Together.ai / Fireworks  pay-per-token, open models     │
│  Modal GPU deployment     self-hosted on Modal, no infra │
│  SGLang on own GPU server air-gap, enterprise on-prem    │
│  Anthropic / Gemini API   simplest to get started        │
└──────────────────────────────────────────────────────────┘
```

The sandbox and the model are fully decoupled. The agent container only needs `OPENAI_BASE_URL` —
it does not care where the model runs.

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

---

## Model setup

The coding agent calls `OPENAI_BASE_URL` — any OpenAI-compatible endpoint works. Pick the option
that fits your team.

### Option A — Together.ai / Fireworks (recommended to start)

No GPU, no infrastructure. Pay per token. Open models, prompts go to their servers for inference
only — not training.

```bash
OPENAI_BASE_URL=https://api.together.xyz/v1
OPENAI_API_KEY=your-together-key
OPENCODE_MODEL=Qwen/Qwen3-Coder-80B-Instruct
```

Cost: ~$0.05–$0.40 per agent run depending on task length.

### Option B — Modal GPU deployment (self-hosted, no own hardware)

Deploy an open model on Modal's GPU infrastructure. You get a stable HTTPS endpoint, scale-to-zero
billing, and no model weights on provider servers after the container goes cold.

```bash
modal deploy modal/serve.py
# → https://your-org--qwen-coder.modal.run/v1

OPENAI_BASE_URL=https://your-org--qwen-coder.modal.run/v1
OPENAI_API_KEY=modal
OPENCODE_MODEL=Qwen/Qwen3-Coder-80B
```

### Option C — Self-hosted SGLang (air-gap, enterprise on-prem)

For regulated environments where prompts cannot leave your network. Run SGLang on your own GPU
server (A100 80GB or 2× RTX 4090 minimum for Qwen3-Coder 80B).

```bash
OPENAI_BASE_URL=http://your-gpu-server:30000/v1
OPENAI_API_KEY=local
OPENCODE_MODEL=Qwen/Qwen3-Coder-80B
```

SGLang's RadixAttention caches the shared system prompt across all agent runs — 40–70% compute
reduction at team scale compared to alternatives.

### Option D — Anthropic / Gemini API

```bash
ANTHROPIC_API_KEY=sk-ant-...
OPENCODE_MODEL=claude-sonnet-4-6
# or
GEMINI_API_KEY=...
OPENCODE_MODEL=gemini-2.5-pro
```

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
agent-run dashboard
# → http://localhost:8080
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
// .claude/settings.json
{
  "mcpServers": {
    "agent-container": {
      "command": "python",
      "args": ["-m", "mcp.server"]
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

## Testing strategy

| Layer | What runs | Cost | Trigger |
|---|---|---|---|
| Unit | Config, spec, result, sandbox (mocked) | $0 | Every commit |
| Integration | Full Modal sandbox lifecycle, stub agent | Modal compute only | Every PR |
| E2e | Real model + fixture repo | ~$0.05/run | Nightly |

---

## Milestones

| Milestone | Scope |
|---|---|
| M1: Core dataclasses | `SandboxConfig`, `AgentTaskSpec`, `AgentTaskResult` ✅ |
| M2: Modal sandbox | `ModalSandbox` boot/run/teardown, CLI |
| M3: Agent internals | OpenCode runner, test detection, git ops |
| M4: Dashboard | FastAPI SSE API, live UI |
| M5: Model serving | `modal/serve.py` — deploy open model on Modal GPU |
| M6: MCP + backends | MCP server, Claude Code + Gemini CLI backends |

---

## Documentation

Full documentation at **[dvdthecoder.github.io/agent-container](https://dvdthecoder.github.io/agent-container)**

- [Architecture](https://dvdthecoder.github.io/agent-container/architecture)
- [Quickstart](https://dvdthecoder.github.io/agent-container/quickstart)
- [Model setup](https://dvdthecoder.github.io/agent-container/models)
- [Enterprise / GitLab](https://dvdthecoder.github.io/agent-container/enterprise)
- [MCP integration](https://dvdthecoder.github.io/agent-container/mcp)
- [Contributing](https://dvdthecoder.github.io/agent-container/contributing)
