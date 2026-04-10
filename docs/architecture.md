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
│  Modal container — one per agent run, ephemeral                  │
│  Coding agent installed: opencode / claude CLI / gemini CLI      │
│  Git + gh / glab CLI for branch and PR operations               │
│                                                                  │
│  Agent calls → OPENAI_BASE_URL                                   │
└──────────────────────────┬───────────────────────────────────────┘
                           │  HTTP  (OpenAI-compatible)
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  Model endpoint — fully pluggable                                │
│                                                                  │
│  Together.ai / Fireworks   open models, pay per token            │
│  Modal GPU deployment      self-hosted open model, no hardware   │
│  SGLang on own server      air-gap, enterprise on-prem           │
│  Anthropic / Gemini API    simplest to start                     │
└──────────────────────────────────────────────────────────────────┘
```

## Key design decisions

### Modal for the sandbox

Every agent run gets a fresh `modal.Sandbox` — a short-lived container on Modal's infrastructure.
This means:

- **No Docker on your machine.** No devcontainer CLI, no Docker Desktop, no `docker info`.
- **Ephemeral by design.** The container destroys itself when the run ends or on error.
- **Parallel runs don't conflict.** Each run has its own isolated filesystem.
- **Scale to zero.** You pay only for the seconds the container is running.

### Pluggable model endpoint

The agent container knows nothing about the model. It injects `OPENAI_BASE_URL`, `OPENAI_API_KEY`,
and `OPENCODE_MODEL` as environment variables into the container. The coding agent picks them up.

This means you can switch model providers with a single env var change — no code changes, no
rebuilds. See [Model Setup](models.md) for options.

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
  opencode --print -m "<task prompt>"     # or claude / gemini equivalent
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
