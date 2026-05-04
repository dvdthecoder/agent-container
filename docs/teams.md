# Running at Team Scale

How to set up agent-container for a team of engineers working on a shared codebase.

---

## The mental model

Each engineer gets their own isolated sandbox per run. There is no shared filesystem,
no shared process, no contention. Five engineers can fire five agent runs simultaneously
against the same repo and they will never interfere with each other — each run clones
a fresh copy of the repo, works in its own container, and opens its own PR.

```
Engineer A  →  Sandbox A  →  PR #101
Engineer B  →  Sandbox B  →  PR #102
Engineer C  →  Sandbox C  →  PR #103
                   ↓
         All call the same model endpoint
         (vLLM on Modal, shared, concurrent)
```

The only shared resources are:

- The **model endpoint** — vLLM serves multiple concurrent requests; `@modal.concurrent(max_inputs=32)` is set in `serve.py`
- The **model weights volume** — read-only at inference time, no contention
- The **`runs.db` log database** — one per machine, local only; if you want a shared view use the dashboard

---

## Infrastructure setup (one-time, per team)

One person deploys the shared model endpoint. Everyone else points their `OPENAI_BASE_URL`
at it.

### 1. Deploy the model endpoint

```bash
# One team member runs this once
SERVE_PROFILE=prod modal deploy modal/serve.py
# → https://your-org--agent-container-serve-serve.modal.run
```

Use `prod` (Qwen3-Coder 80B) for a real codebase — the 32B test model will struggle with
large files, complex reasoning, or multi-file changes.

### 2. Share the endpoint URL

Add it to your team's shared secrets manager (1Password, Vault, GitHub Actions secrets):

```bash
OPENAI_BASE_URL=https://your-org--agent-container-serve-serve.modal.run
OPENAI_API_KEY=modal
OPENCODE_MODEL=qwen3-coder
```

### 3. Each engineer configures locally

```bash
cp .env.example .env
# fill in MODAL_TOKEN_ID, MODAL_TOKEN_SECRET, GITHUB_TOKEN
# paste the shared OPENAI_BASE_URL from step 2
```

---

## Day-to-day workflow

### Pattern 1 — Engineer delegates a task

An engineer has a well-understood task but doesn't want to context-switch:

```bash
agent-run run \
  --repo https://github.com/org/myapp \
  --task "Add pagination to GET /api/users — use cursor-based pagination, \
          update the OpenAPI spec, add tests" \
  --backend aider \
  --timeout 600
```

The agent opens a PR. The engineer reviews it when they come up for air.

### Pattern 2 — Bug triage from an issue

Copy the issue description directly as the task. The agent reads the repo, finds the
relevant code, and proposes a fix:

```bash
agent-run run \
  --repo https://github.com/org/myapp \
  --task-file tasks/issue-483.md \
  --backend opencode
```

`--task-file` accepts a Markdown file — paste the full issue body, reproduction steps,
expected behaviour. opencode's multi-turn loop can read files, run tests, and iterate
before committing.

### Pattern 3 — Run tests and fix failures

Useful when a dependency upgrade breaks things:

```bash
agent-run run \
  --repo https://github.com/org/myapp \
  --task "pytest is failing after the requests 2.32 upgrade — fix all failures" \
  --backend opencode \
  --timeout 900
```

opencode runs `pytest`, reads the failure output, traces it to the root cause, and patches
the code. It then runs `pytest` again to verify before committing.

### Pattern 4 — Apply the same change across multiple repos

```python
from sandbox import ModalSandbox, SandboxConfig
from sandbox.spec import AgentTaskSpec
from concurrent.futures import ThreadPoolExecutor

config = SandboxConfig.from_env()
repos = [
    "https://github.com/org/service-a",
    "https://github.com/org/service-b",
    "https://github.com/org/service-c",
    "https://github.com/org/service-d",
    "https://github.com/org/service-e",
]

def run(repo: str):
    return ModalSandbox(config).run(AgentTaskSpec(
        repo=repo,
        task="Add OWASP-recommended security headers to all HTTP responses",
        backend="aider",
        create_pr=True,
    ))

with ThreadPoolExecutor(max_workers=5) as pool:
    results = list(pool.map(run, repos))

for r in results:
    print(r.pr_url, r.diff_stat)
```

Five PRs across five repos in parallel — each in its own isolated sandbox.

---

## Dashboard for team visibility

Start one dashboard instance for the team:

```bash
DASHBOARD_HOST=0.0.0.0 make dashboard
# → http://your-machine:8000
```

Everyone on the team can open the dashboard URL to see all running, completed, and failed
runs in real time — phase indicators, live log stream, PR links. No setup needed on the
viewer side.

!!! warning
    The dashboard has no authentication. Run it behind a reverse proxy with auth (nginx,
    Caddy) if your network is not fully trusted.

---

## PR review process

The agent always opens a PR — it never merges. Every change goes through your normal review
process. Suggested branch protection rules for agent PRs:

```
Branch pattern:  agent/*
Required reviews: 1
Dismiss stale reviews: true
Require status checks: CI must pass
```

This ensures:

- A human reads every agent diff before it lands
- CI runs against agent branches the same as human branches
- A stale agent PR is not auto-merged if main has moved on

---

## Cost at team scale

### Model endpoint

With `SERVE_PROFILE=prod` (Qwen3-Coder 80B on 2× A100 80GB):

- Cost: ~$8–10/hr while active
- Scale-to-zero after 10 min idle — you pay nothing when no runs are active
- 5 concurrent runs share the same endpoint — no need to deploy 5 separate models

A typical agent run is 2–5 min of inference time. At $8/hr that's ~$0.27–$0.67 per run.
Five engineers running two tasks each per day = ~$2.70–$6.70/day in model costs.

### Sandbox containers

Modal sandbox containers (A10G equivalent for the agent runtime) are ~$0.10–$0.20 per run.
The sandbox cost is small relative to the model cost.

### Reducing cost

- Use `SERVE_MODEL=qwen3-8b` or `qwen2.5-coder-32b` (default) for simple, well-specified tasks — 8× cheaper than 80B models
- Use `--backend aider` for targeted edits — fewer model calls than opencode
- Use `--no-pr` during iteration to skip git push/PR overhead
- Set `--timeout` appropriately — a stuck run burns GPU time

---

## Logging across the team

Each engineer's `~/.agent-container/runs.db` is local. To query across the team, either:

**Option A — shared network path:**

```bash
# Engineer running shared infra
RUNS_DB=/shared/path/runs.db agent-run run ...

# Others query
agent-run logs --db /shared/path/runs.db
```

**Option B — SQLite directly:**

```bash
sqlite3 ~/.agent-container/runs.db \
  "SELECT run_id, repo, outcome, pr_url FROM runs ORDER BY started_at DESC LIMIT 20"
```

See [Run Logs](logging.md) for the full query reference.

---

## Production gaps and roadmap

This section is an honest account of where the current implementation stands relative to
a fully production-hardened team deployment, informed by how teams like Ramp have scaled
similar systems.

### What is working and production-ready today

The core pipeline is solid — Modal sandbox isolation, vLLM inference, aider/opencode backends,
test suite execution, PR creation, SQLite logging, and the dashboard. Teams can use this today
for delegating well-understood tasks and applying changes across repos at scale.

### Gap 1 — Cold sandbox startup

**Current state:** Every run does a full `git clone` + dependency install from scratch.
For a repo with a heavy install step (large Python project, node_modules, Rust workspace)
this can add 3–10 minutes of dead time before the agent writes a single line.

**Production approach:** Pre-build a repo image on a schedule (e.g. every 30 minutes) with
dependencies installed, then snapshot it using Modal's snapshot API. New sandboxes restore
from the snapshot — boot time drops to seconds. The agent starts working against at most 30
minutes of stale state, which is then fast-forwarded with a `git pull`.

**Planned:** This is the highest-leverage improvement for real-codebase usage. Not yet
implemented.

### Gap 2 — Verification loop depth

**Current state:** The TESTING phase auto-detects and runs the test suite (pytest / npm /
cargo / go) and includes the result in the PR description. If tests pass, the run succeeds.

**Production approach:** Closing the loop means more than green tests. For backend changes:
check that no new Sentry errors appeared after the change, verify that the relevant feature
flag is gating the code path correctly, confirm that Datadog metrics are not degraded.
For frontend changes: take a screenshot of the affected component, diff it visually against
the baseline, include the screenshot in the PR.

**Planned:** Deeper verification hooks are a natural extension of the TESTING phase. The
phase currently runs one command — it can be extended to run a sequence of verification
steps configurable per repo.

### Gap 3 — Session persistence and resumption

**Current state:** Runs are fire-and-forget. Once a run starts it runs to completion or
timeout. There is no way to pause it, inspect the intermediate state, provide feedback,
and resume. The result is binary: success or failure.

**Production approach:** A persistent session model where the sandbox stays alive between
turns, the engineer can comment on intermediate work, and the agent continues from that
context. This is closer to a pair programming session than a batch job.

**Not planned short-term:** This requires a different execution model (long-lived sandbox,
bidirectional communication channel, session state management). The current fire-and-forget
model is simpler and covers the majority of automation use cases.

### Gap 4 — Multiple entry points

**Current state:** Entry points are CLI, Python API, MCP tool, and dashboard — all
developer-facing and requiring terminal or IDE access.

**Production approach:** A Slack bot lets any team member fire a run from a message,
with automatic repo detection from conversation context. A Chrome extension enables visual
component editing without leaving the browser. A web UI with a hosted editor inside the
sandbox lets non-engineers participate in the workflow.

**Partially addressed:** The MCP integration covers Claude Code and Gemini CLI sessions.
A Slack bot and web UI are natural next steps for broader team adoption.

### Gap 5 — Multiplayer sessions

**Current state:** Each run is owned by one engineer. There is no concept of a shared
session where multiple people can observe the agent's work, inject context, or take over.

**Production approach:** Multiple engineers join a single session. Code review happens
inside the live session — the reviewer can see the agent's reasoning in real time, ask it
to change approach, and the final commit is attributed to the session participants.

**Not planned short-term:** Requires the session persistence model (Gap 3) as a
prerequisite.

### Summary

| Capability | Status |
|---|---|
| Isolated concurrent runs | ✅ Production-ready |
| Self-hosted model (cost advantage) | ✅ Production-ready |
| Test suite execution | ✅ Production-ready |
| PR creation (GitHub + GitLab) | ✅ Production-ready |
| Dashboard + structured logging | ✅ Production-ready |
| Warm sandboxes (pre-built snapshots) | Planned |
| Deep verification (Sentry, metrics, screenshots) | Planned |
| Slack bot / web UI | Planned |
| Session persistence and resumption | Not planned short-term |
| Multiplayer sessions | Not planned short-term |
