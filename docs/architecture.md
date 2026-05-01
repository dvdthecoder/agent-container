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

Scale-to-zero when idle, billed per GPU second, no hardware to manage.

### vLLM — primary inference engine

[vLLM](https://github.com/vllm-project/vllm) is the default inference server for all three
production profiles (`test`, `prod`, `minimax`). It provides a stable OpenAI-compatible
`/v1/chat/completions` API with first-class tool calling (`--enable-auto-tool-choice
--tool-call-parser hermes`), tensor parallelism for multi-GPU models, and KV prefix caching.

vLLM works with a standard Python base image (`debian_slim`) — no CUDA toolkit or driver
installation required. Modal injects GPU drivers at runtime.

**Why vLLM and not SGLang initially:** The original implementation used SGLang v0.4.7 as
the inference server. It had multiple blocking bugs at that version: `--enable-auto-tool-choice`
did not exist, `--tool-call-parser qwen25` crashed the server on the first tool-schema
request, and streaming with tools hung indefinitely. Phase 1 switched to vLLM and removed
all SGLang-specific workarounds from the proxy layer (389 lines removed in Phase 2).

### SGLang — validated alternative (Phase 3)

[SGLang](https://github.com/sgl-project/sglang) was re-evaluated in Phase 3 against the same
model (Qwen2.5-Coder 7B on A10G) to determine whether it had fixed the tool-calling bugs.

**Finding:** SGLang works end-to-end with `--tool-call-parser hermes`. The `qwen`/`qwen25`
parsers still hang on tool-schema requests. First tool call with 10 tools: 3 seconds. Full
run to PR: 29 seconds.

SGLang requires more image setup than vLLM — a CUDA devel base image, `libnuma1`, and
`--disable-cuda-graph` on first boot. See [Model Setup → SGLang](models.md#sglang--phase-3-validation-results)
for the complete setup.

The `sglang` profile deploys to a **separate Modal app** (`agent-container-serve-sglang`) so
both inference servers can run simultaneously without interfering:

```
agent-container-serve        → vLLM  (default, all production profiles)
agent-container-serve-sglang → SGLang (SERVE_PROFILE=sglang, hermes parser)
```

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
  # write __pycache__/*.pyc patterns to .git/info/exclude (local-only, never committed)

Step 2 — Agent runs
  aider --yes --map-tokens 1024 --message "<task>" /workspace   # or opencode equivalent
  streams output live to dashboard via SSE

Step 3 — Collect result
  git diff origin/<base_branch>           # full diff (includes aider commits)
  git diff --stat origin/<base_branch>    # summary line

Step 4 — Open PR  (if create_pr=True)
  git checkout -b agent/<backend>-<timestamp>
  git add -A && git commit -m "agent: <task>"   # skipped if agent already committed
  git push origin agent/<backend>-<timestamp>
  curl POST <provider_api>/pulls           # GitHub or GitLab REST API

Step 5 — Container destroyed
  sb.terminate(wait=True)                 # blocks until Modal confirms container stopped
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
