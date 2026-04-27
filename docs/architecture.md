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
│  Coding agent installed: opencode / claude CLI / gemini CLI      │
│  Git + gh / glab CLI for branch and PR operations               │
│                                                                  │
│  Agent calls → Modal model endpoint (internal network)           │
└──────────────────────────┬───────────────────────────────────────┘
                           │  Modal internal network
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  Modal GPU — SGLang inference server                             │
│  modal deploy modal/serve.py  →  stable endpoint                 │
│  Qwen3-Coder on A100 · RadixAttention prefix caching             │
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
deploys Qwen3-Coder once and gives you a stable internal endpoint. The sandbox container calls
it over Modal's internal network — no public internet hop, no external API key needed.

The inference server is [SGLang](https://github.com/sgl-project/sglang). Its **RadixAttention**
automatically caches shared KV prefixes across requests. Agent runs that share a system prompt and
repo context (the common case) hit the prefix cache on every run after the first, improving
throughput and reducing per-token latency under concurrent load.

Scale-to-zero when idle. Billed per GPU second. No hardware to manage.

### Coding agent as a plugin

OpenCode is the default backend, but Claude Code CLI and Gemini CLI can run inside the container
instead. All backends produce the same `AgentTaskResult` — the rest of the pipeline is identical.
See [Agent Backends](agents.md).

## What runs inside the container

```
Step 1 — Clone
  git clone <repo> --branch <base_branch> --depth 1
  git checkout -b agent/<slug>-<timestamp>

Step 2 — Agent runs
  python3 /opencode_runner.py "<task prompt>"   # ACP-based; or claude / gemini equivalent
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

## File structure

```
agent_container/
├── sandbox/
│   ├── config.py        SandboxConfig — Modal auth + defaults
│   ├── spec.py          AgentTaskSpec — task description
│   ├── result.py        AgentTaskResult + SuiteResult
│   └── sandbox.py       ModalSandbox — boot/run/teardown
├── modal/
│   ├── sandbox.py       Modal Sandbox implementation
│   └── serve.py         Deploy open model on Modal GPU
├── agent/
│   ├── cli.py           agent-run CLI entrypoint
│   ├── installer.py     Install agent CLI inside container
│   ├── runner.py        Invoke agent non-interactively
│   ├── tester.py        Auto-detect and run test suite
│   ├── git_ops.py       clone, branch, diff, push, PR
│   └── backends/        AgentBackend protocol + adapters
├── dashboard/
│   ├── app.py           FastAPI app
│   ├── store.py         WorkspaceStore + RunState
│   ├── router.py        REST + SSE route handlers
│   └── static/
│       └── index.html   Dashboard UI (vanilla JS, no build step)
└── mcp/
    └── server.py        MCP server exposing sandbox tools
```
