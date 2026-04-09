# agent-container

A secure, ephemeral sandbox for autonomous coding agents. Give it a task and a repo — it boots a
fresh workspace, runs an AI coding agent inside it, tests the result, opens a PR, and destroys the
workspace. Nothing persists. Nothing leaks.

Built on [Daytona](https://daytona.io) for workspace orchestration and
[OpenCode](https://opencode.ai) as the coding agent.

---

## What it does

```
$ agent-run \
    --task "Add rate limiting to /api/login — max 5 requests/min per IP" \
    --repo https://github.com/org/myapp

  booting workspace...
  cloning repo...
  running opencode...
  running tests... 24 passed
  opening PR...

  ✓ Done in 2m 14s
  PR: https://github.com/org/myapp/pull/42   +67 −3
```

A fresh Daytona workspace boots with the specified Docker image, the agent clones the repo and makes
the changes, tests run to verify, a PR is opened, and the workspace is destroyed. The agent never
touches your local machine.

---

## Architecture

### System layers

```
┌──────────────────────────────────────────────────────────────────┐
│  Browser — Agent Sandbox Dashboard  (localhost:8080)             │
│  Live view of all running, completed, and failed agent tasks     │
└────────────────────────────┬─────────────────────────────────────┘
                             │ SSE + REST
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  FastAPI dashboard server                                        │
│  WorkspaceStore — in-memory run state, log ring buffer           │
│  SSE streaming — phase changes + live logs to browser            │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  DaytonaAgentSandbox — lifecycle orchestration                   │
│  boot → clone → opencode → test → PR → teardown                  │
│  Teardown runs in finally block — workspace never left dangling  │
└────────────────────────────┬─────────────────────────────────────┘
                             │ Daytona Python SDK
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  Daytona OSS (self-hosted, localhost:3986)                       │
│  One ephemeral workspace per agent run                           │
│  Docker image specified per task — any language runtime          │
└──────────────────────────────────────────────────────────────────┘
```

### What runs inside each workspace

```
Step 1 — Setup
  git clone <repo>
  git checkout -b agent/<slug>-<timestamp>
  install opencode + gh CLI (or skip if pre-baked in image)

Step 2 — Agent runs
  opencode --print -m "<task prompt>"
  streams output live to dashboard

Step 3 — Tests
  auto-detect runner: pytest / npm test / go test / cargo test / make test
  capture pass/fail + output

Step 4 — Collect and ship
  git diff → unified diff string
  git push + gh pr create → PR URL
  workspace destroyed
```

### File structure

```
agent_container/
├── sandbox/
│   ├── config.py          # SandboxConfig — Daytona connection + defaults
│   ├── spec.py            # AgentTaskSpec — task, repo, image, env, flags
│   ├── result.py          # AgentTaskResult — pr_url, diff, tests, timing
│   └── sandbox.py         # DaytonaAgentSandbox — boot/run/teardown lifecycle
├── agent/
│   ├── installer.py       # Install opencode + gh CLI inside workspace
│   ├── runner.py          # Invoke opencode non-interactively
│   ├── tester.py          # Auto-detect and run test suite
│   └── git_ops.py         # clone, branch, diff, push, gh pr create
├── dashboard/
│   ├── app.py             # FastAPI app
│   ├── store.py           # WorkspaceStore + RunState
│   ├── router.py          # REST + SSE route handlers
│   └── static/
│       └── index.html     # Single-file dashboard UI (vanilla JS, no build step)
├── cli.py                 # agent-run CLI entrypoint
├── pyproject.toml
└── .env.example
```

---

## Developer workflow

### 1. One-time setup

```bash
git clone https://github.com/dvdthecoder/agent-container
cd agent-container
pip install -e .

cp .env.example .env
# fill in DAYTONA_SERVER_URL, DAYTONA_API_KEY, GITHUB_TOKEN, and model config

daytona serve   # start Daytona OSS server
```

### 2. Start the dashboard

```bash
agent-run dashboard
# opens http://localhost:8080
```

### 3. Fire a task

```bash
# from the CLI
agent-run \
  --task "Add rate limiting to /api/login" \
  --repo https://github.com/org/myapp

# or from the dashboard UI — click + New Run, fill in the form
```

### 4. Watch it run

The dashboard shows each run as a card with a live phase indicator:

```
● BOOTING     cloning repo...
● CLONING     git clone https://github.com/org/myapp
● RUNNING     [opencode] Reading api/login.py...
◉ TESTING     pytest ... 24 passed in 3.4s
↑ OPENING PR  gh pr create...
✓ DONE        PR #42   +67 −3
```

Logs stream in real time via Server-Sent Events. The workspace is destroyed the moment the run
completes or fails.

### 5. Python API (for programmatic use)

```python
from sandbox import DaytonaAgentSandbox, SandboxConfig, AgentTaskSpec

config = SandboxConfig.from_env()

spec = AgentTaskSpec(
    repo="https://github.com/org/myapp",
    task="Add rate limiting to /api/login — max 5 req/min per IP",
    base_branch="main",
    create_pr=True,
)

async with DaytonaAgentSandbox(config) as sandbox:
    result = await sandbox.run(spec)

print(result.pr_url)        # https://github.com/org/myapp/pull/42
print(result.diff_stat)     # +67 −3
print(result.tests.passed)  # 24
```

---

## Model configuration

The coding agent (OpenCode) needs an LLM. The workspace receives the model endpoint as an env var —
the sandbox orchestrator has no direct dependency on any LLM provider.

### Option A — Self-hosted (privacy-first, recommended for teams)

Run [Qwen3-Coder 80B](https://huggingface.co/Qwen/Qwen3-Coder-80B) on a shared team inference
server. No code leaves your network.

```bash
# .env
OPENAI_BASE_URL=http://192.168.1.50:11434/v1
OPENAI_API_KEY=local
OPENCODE_MODEL=qwen3-coder:80b
```

Start the inference server:

```bash
# Ollama (easiest)
docker compose up inference-server

# vLLM (better throughput for concurrent runs)
vllm serve Qwen/Qwen3-Coder-80B --tensor-parallel-size 2 --port 11434
```

### Option B — Serverless open model (no GPU, pay-per-use)

Use Together.ai or Fireworks.ai to run Qwen3-Coder without managing hardware. Same model, same
privacy posture for the code (only the prompt leaves your network, not the repo structure).

```bash
OPENAI_BASE_URL=https://api.together.xyz/v1
OPENAI_API_KEY=your-together-api-key
OPENCODE_MODEL=Qwen/Qwen3-Coder-80B-Instruct
```

Cost: ~$0.05–0.40 per agent run depending on task length.

### Option C — Cloud API (simplest to get started)

```bash
ANTHROPIC_API_KEY=sk-ant-...
OPENCODE_MODEL=claude-sonnet-4-6
```

### Model selection rationale

Qwen3-Coder 80B scores **70.6 on SWE-bench** (real code editing on real repos) and is purpose-built
for software engineering tasks. It fits on a single A100 80GB or two RTX 4090s.
[Onyx self-hosted LLM leaderboard](https://onyx.app/self-hosted-llm-leaderboard) is a useful
reference for tracking how open models compare on coding tasks.

### Model hosting topologies

The inference server is just a URL injected into each Daytona workspace. Four topologies work with
zero code changes — only the env var changes:

| Topology | OPENAI_BASE_URL | Best for |
|---|---|---|
| Laptop (Ollama) | `http://host.docker.internal:11434/v1` | Solo dev with GPU |
| Team server (LAN) | `http://192.168.1.50:11434/v1` | Team, shared GPU |
| Cloud GPU (same VPC) | `http://10.0.1.50:11434/v1` | Cloud Daytona workspaces |
| Modal / serverless | `https://org--model.modal.run/v1` | No hardware, scale-to-zero |

**Daytona vs Modal for model hosting:** Daytona creates ephemeral dev environments (CPU, short-lived).
It is not a GPU compute platform and cannot host a model the way Modal can. Modal is the right tool
for serverless model hosting — Daytona workspaces call Modal's inference endpoint over HTTP, same as
any other provider. They are complementary, not competing.

---

## Enterprise use (GitLab)

Swap `gh` for `glab`. PRs become Merge Requests. Everything else is identical.

```bash
GITLAB_TOKEN=glpat-...
# git_ops.py uses glab mr create instead of gh pr create
```

### GitLab-native integration patterns

**Issue-triggered runs** — label a GitLab issue `agent-run`, a webhook fires, the sandbox picks it
up, creates a branch, opens an MR referencing the issue. Developer reviews and merges.

**Cross-repo changes at scale** — the same task description run against 50 microservice repos
produces 50 MRs in parallel. Systematic changes (dependency upgrades, security patches, API
migrations) that would take a team weeks take an afternoon.

**GitLab CI trigger**

```yaml
agent-fix:
  stage: maintenance
  trigger: manual
  script:
    - agent-run --task "$AGENT_TASK" --repo "$CI_PROJECT_URL"
```

**Security patching** — a CVE drops, security team labels all affected repos, MRs with the patch
are open by morning.

### Privacy guarantee (fully air-gapped)

GitLab on-prem + Daytona on-prem + Qwen3-Coder on-prem = no line of code, no prompt, no diff
ever touches the public internet. This is the correct architecture for regulated industries
(finance, healthcare, government).

Every agent action produces a GitLab MR with full diff, test results, and the original task prompt
in the description — a complete audit trail. MRs require human approval before merge. The agent
proposes, humans decide.

---

## Cost analysis

### Infrastructure cost centres

**Inference server** (the significant one)

| Setup | Upfront | Per-run cost | Break-even vs cloud API |
|---|---|---|---|
| Cloud API (Haiku) | $0 | ~$0.05–0.10 | N/A |
| Cloud API (Sonnet) | $0 | ~$0.30–0.80 | N/A |
| Together.ai (Qwen3) | $0 | ~$0.05–0.40 | N/A |
| 2x RTX 4090 (on-prem) | ~$3,200 | ~$0.002 | ~20 runs/day |
| A100 80GB (on-prem) | ~$12,000 | ~$0.005 | ~40 runs/day |
| A100 80GB (cloud GPU) | $0 upfront | ~$0.08/run | varies |

**Workspace compute** (negligible) — each workspace lives 2–5 minutes, 2–4 vCPU, 8GB RAM. Under
$0.02/run on cloud. Near zero on self-hosted.

### At enterprise scale (50 devs, 10 tasks/day each)

| | Cloud API | Self-hosted LLM |
|---|---|---|
| Runs/day | 500 | 500 |
| Cost/day | ~$1,000 | ~$15 |
| Cost/year | ~$365,000 | ~$40,000 |
| Savings | — | ~$325,000/year |

Hardware pays for itself in 2–3 weeks at that volume.

---

## Testing strategy

### Test pyramid

**Unit tests (free, every commit)**

Tests that need no external services — config validation, dataclass serialisation, dashboard store
logic, test runner detection heuristics, SSE event formatting.

**Integration tests (near-zero cost, every PR)**

Workspace lifecycle tests using a stub agent instead of OpenCode:

```bash
# stub_agent.sh — dropped into workspace instead of opencode
echo "fix = True" >> math.py
git add . && git commit -m "agent: apply fix"
echo '{"status": "done"}'
```

Tests boot/teardown, file writes, env var injection, diff collection — without spending a token.
Requires a Daytona instance accessible from CI.

**End-to-end tests (low cost, nightly)**

Full pipeline with a real model against a fixture repo. The fixture has a deliberate off-by-one bug
and a failing test. The task is trivial enough that any capable model fixes it deterministically.

Use `claude-haiku-4-5` in CI — ~$0.05–0.10/run. At nightly frequency: ~$3/month.
Never run e2e on every commit.

### CI cost summary

| Layer | Monthly cost | Trigger |
|---|---|---|
| Unit | $0 | Every commit |
| Integration (stub) | $0 + infra | Every PR |
| E2e (Haiku) | ~$3 | Nightly |
| E2e (local model) | ~$0 | On-demand |

---

## Milestones

| Milestone | Scope |
|---|---|
| [M1: Sandbox Core](https://github.com/dvdthecoder/agent-container/milestone/1) | Daytona lifecycle, config, spec, result |
| [M2: Agent Internals](https://github.com/dvdthecoder/agent-container/milestone/2) | OpenCode runner, test detection, git ops |
| [M3: Dashboard](https://github.com/dvdthecoder/agent-container/milestone/3) | FastAPI SSE API, live dashboard UI |
| [M4: Self-hosted LLM](https://github.com/dvdthecoder/agent-container/milestone/4) | Provider abstraction, Ollama/vLLM, Modal option |
| [M5: CLI & Integration](https://github.com/dvdthecoder/agent-container/milestone/5) | agent-run CLI, e2e tests, examples |

---

## Environment variables

```bash
# Daytona OSS
DAYTONA_SERVER_URL=http://localhost:3986
DAYTONA_API_KEY=your-daytona-api-key

# GitHub / GitLab (PR + MR creation inside workspace)
GITHUB_TOKEN=ghp_...
# GITLAB_TOKEN=glpat-...

# Model provider — pick one mode:

# Self-hosted (privacy-first)
OPENAI_BASE_URL=http://192.168.1.50:11434/v1
OPENAI_API_KEY=local
OPENCODE_MODEL=qwen3-coder:80b

# Serverless open model (no GPU required)
# OPENAI_BASE_URL=https://api.together.xyz/v1
# OPENAI_API_KEY=your-together-key
# OPENCODE_MODEL=Qwen/Qwen3-Coder-80B-Instruct

# Cloud API (simplest to get started)
# ANTHROPIC_API_KEY=sk-ant-...
# OPENCODE_MODEL=claude-sonnet-4-6
```
