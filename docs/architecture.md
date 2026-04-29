# Architecture

## System layers

```
┌──────────────────────────────────────────────────────────────────┐
│  Interfaces                                                      │
│  agent-run CLI · Dashboard (FastAPI + SSE) · Python API          │
│  MCP server  →  Claude Code / Gemini CLI sessions                │
└──────────────────────────┬───────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  ModalSandbox — lifecycle orchestration                          │
│  boot → clone → run agent → collect diff → PR → destroy          │
│  Destroy always runs (finally block) — no dangling containers    │
└──────────────────────────┬───────────────────────────────────────┘
                           │  modal.Sandbox (Python SDK)
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  Modal — container compute                                       │
│  Each run gets a fresh ephemeral container                       │
│  Agent backends: aider · opencode · claude CLI · gemini CLI      │
│  Git + gh CLI for branch and PR operations                       │
│                                                                  │
│  Agent calls → Modal model endpoint (internal network)           │
└──────────────────────────┬───────────────────────────────────────┘
                           │  POST /v1/chat/completions
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  Modal GPU — vLLM inference server                               │
│  modal deploy modal/serve.py  →  stable endpoint                 │
│  Qwen3-Coder · MiniMax M2.5 — first-class tool calling           │
│  Scale-to-zero when idle, billed per GPU second                  │
└──────────────────────────────────────────────────────────────────┘
```

Everything runs on Modal. The sandbox container and the model are both Modal resources — they
communicate over Modal's internal network without touching the public internet.

## Key design decisions

### Modal for the sandbox

Every agent run gets a fresh `modal.Sandbox` — a short-lived container on Modal's infrastructure.
This means:

- **No Docker on your machine.** No devcontainer CLI, no Docker Desktop, no `docker info`.
- **Ephemeral by design.** The container destroys itself when the run ends or on error.
- **Parallel runs don't conflict.** Each run has its own isolated filesystem.
- **Scale to zero.** You pay only for the seconds the container is running.

### Modal for model serving

The model runs on Modal GPU infrastructure alongside the sandbox. `modal deploy modal/serve.py`
deploys the model once and gives you a stable internal endpoint. The sandbox container calls
it over Modal's internal network — no public internet hop, no external API key needed.

The inference server is **vLLM**. It provides a stable, first-class OpenAI-compatible
`/v1/chat/completions` API with reliable tool calling across all model profiles. Scale-to-zero
when idle, billed per GPU second, no hardware to manage.

### Agent backends

Two backends are supported. Both produce identical `AgentTaskResult` output — PR creation,
the dashboard, and MCP integration work identically regardless of which backend you use.

**aider** (default) calls `/v1/chat/completions` directly. No proxy, no translation layer.
Uses text-based diff editing — the model returns structured diffs, aider applies them. Works
reliably with any OpenAI-compatible endpoint.

**opencode** uses a thin Responses API adapter. opencode v1.14+ calls `/v1/responses`
(OpenAI Responses API); the adapter translates to Chat Completions and back. The adapter
is ~100 lines of pure JSON reshaping with no model-specific code.

See [Agent Backends](agents.md) for full documentation.

## What runs inside the container

```
Step 1 — Clone
  git clone <repo> --branch <base_branch> --depth 1
  git checkout -b agent/<slug>-<timestamp>

Step 2 — Agent runs
  aider --yes --message "<task>" /workspace     # or opencode equivalent
  streams output live to dashboard via SSE

Step 3 — Collect result
  git diff HEAD                           # full diff string
  git diff --stat HEAD                    # summary line

Step 4 — Open PR  (if create_pr=True)
  git push origin agent/<slug>-<timestamp>
  gh pr create --title "agent: <task>" --body "<diff_stat>"

Step 5 — Container destroyed
```

## Data flow

```
AgentTaskSpec
  repo, task, base_branch, image, env, timeout_seconds, backend, create_pr
        ↓
ModalSandbox.run(spec)
        ↓
AgentTaskResult
  success, run_id, branch, pr_url, diff, diff_stat, duration_seconds, error, backend
```

## opencode adapter (thin proxy)

opencode v1.14+ uses the OpenAI Responses API (`POST /v1/responses`). No self-hosted inference
server implements this API — they all speak Chat Completions. The adapter bridges the gap:

```
opencode → POST /v1/responses
                ↓  reshape: input[] → messages[], tools format, role names
           POST /v1/chat/completions  →  vLLM
                ↑  reshape: tool_calls → function_call items
opencode ← Responses API response
```

The adapter contains no model-specific code. It is a format translation layer only.

## File structure

```
agent_container/
├── sandbox/
│   ├── config.py        SandboxConfig — Modal auth + defaults
│   ├── spec.py          AgentTaskSpec — task description
│   ├── result.py        AgentTaskResult + SuiteResult
│   └── sandbox.py       ModalSandbox — boot/run/teardown
├── modal/
│   └── serve.py         Deploy open model on Modal GPU (vLLM)
├── agent/
│   ├── cli.py           agent-run CLI entrypoint
│   ├── runner.py        Invoke agent, stream output, enforce timeout
│   ├── tester.py        Auto-detect and run test suite
│   ├── git_ops.py       clone, branch, diff, push, PR
│   ├── log_store.py     SQLite run logger (RunLogger + RunStore)
│   ├── opencode_runner.py  Responses API adapter (opencode backend only)
│   └── backends/        AgentBackend protocol + adapters
│       ├── aider.py     aider — direct Chat Completions
│       ├── opencode.py  opencode — via Responses API adapter
│       ├── claude_code.py
│       └── gemini.py
├── dashboard/
│   ├── app.py           FastAPI app
│   ├── store.py         WorkspaceStore + RunState
│   └── router.py        REST + SSE route handlers
└── mcp_server/
    └── server.py        MCP server exposing sandbox tools
```
