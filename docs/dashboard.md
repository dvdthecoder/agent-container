# Dashboard

The dashboard is a live admin view of all agent runs — started from the CLI or the dashboard,
running, completed, and failed — in one unified list.

## Start

```bash
make dashboard
# or: uvicorn dashboard.app:app --reload --port 8000
# → http://localhost:8000
```

## Layout

```
┌─────────────────────┬────────────────────────────────────────────────┐
│  Left sidebar       │  [ Runs ] [ Tokens ]  ← tab bar                │
│                     ├────────────────────────────────────────────────┤
│  ┌───────────────┐  │  RUNS TAB                                      │
│  │ Model server  │  │  ▶ ● run-20260501-123456  org/repo  aider  cli │
│  │ profile ▾     │  │  ▶ ● run-20260501-120000  org/repo  opencode   │
│  │ [Deploy]      │  │  ▼ ● run-20260501-110000  org/repo  aider      │
│  └───────────────┘  │    ┌─ expanded ─────────────────────────────┐  │
│                     │    │ branch: main  task: fix pagination...  │  │
│  ┌───────────────┐  │    │ 12:01:04 ▶ BOOTING                     │  │
│  │ New run       │  │    │ 12:01:15 ▶ RUNNING                     │  │
│  │ repo ______   │  │    │ 12:02:30 ✓ DONE  +12 −3                │  │
│  │ task ______   │  │    └────────────────────────────────────────┘  │
│  │ backend ▾     │  ├────────────────────────────────────────────────┤
│  │ [Start run]   │  │  TOKENS TAB                                    │
│  └───────────────┘  │  backend ▾  from ____  to ____  $/1M [1.00]   │
│                     │  Run ID  Repo  Backend  Prompt  Completion  …  │
│                     │  run-20… org/… opencode  12,345   1,234  …     │
└─────────────────────┴────────────────────────────────────────────────┘
```

**Left sidebar** holds two panels:
- **Model server panel** — profile/model selector and Deploy button (see [Serve panel](#serve-panel))
- **New run form** — repo URL, task, backend, base branch, options

**Main area** has a tab bar at the top:

### Runs tab

Full-width scrollable list of run rows. Each row is collapsible:

| State | What you see |
|---|---|
| Collapsed | Phase dot · run ID · repo · backend badge · `cli`/`dashboard` badge · phase label |
| Expanded | Meta bar (branch, task excerpt, PR link) + inline log stream (scrollable, 400px max) |

Click anywhere on a row summary to expand or collapse it. New runs auto-expand and stream
logs as they arrive. Rows remain in the list after completion — collapse them to keep the view clean.

### Tokens tab

Per-run token consumption for all completed runs that have token data (opencode via the Responses
API proxy; other backends emit no usage data unless they log the `[runner] token_usage:` line).

| Column | Description |
|---|---|
| Run ID | Links the row to the run |
| Repository | Short form (org/repo) |
| Backend | Badge (aider / opencode / …) |
| Outcome | success / error / failed |
| Prompt tokens | Input tokens charged |
| Completion tokens | Output tokens charged |
| Total tokens | Sum |
| Est. cost | `total_tokens / 1M × rate` — rate is configurable in the toolbar |
| Duration | Wall-clock seconds for the run |

**Toolbar controls:**
- **Backend filter** — narrow to one backend for apples-to-apples comparisons
- **From / To date pickers** — restrict to a date range
- **$/1M tokens** — cost rate input; updating it recalculates the Est. cost column live without reloading data
- **Summary bar** — shows `N runs · X total tokens · est. $Y` for the current filtered set

Click any column header to sort. Clicking the same header again reverses the sort direction.

## `initiated_by` badge

Every run row shows where the run was started:

- `cli` — started via `agent-run run` or the Python API
- `dashboard` — started from the new-run form in the browser

Both sources write to the same SQLite log at `~/.agent-container/runs.db`. The list on page
load comes from SQLite, so CLI runs appear alongside dashboard runs automatically.

## Run phases

| Phase | Meaning |
|---|---|
| `WARMING` | Polling `GET /v1/models` until the inference endpoint is ready |
| `BOOTING` | Modal sandbox container starting |
| `CLONING` | `git clone` running inside container |
| `RUNNING` | Coding agent executing |
| `TESTING` | Test suite auto-detected and running |
| `PR` | Creating branch and opening PR |
| `DONE` | Run complete, container destroyed |
| `FAILED` | Error occurred, container destroyed |

## Serve panel

The serve panel in the sidebar lets you deploy or redeploy the model server without touching
the terminal.

**Profile options:**

| Option | Profile + model |
|---|---|
| test — Qwen2.5-Coder 32B (A100 80GB) | `SERVE_PROFILE=test` |
| prod — Qwen3-Coder 80B (2×A100) | `SERVE_PROFILE=prod` |
| prod — MiniMax M2.5 (8×A100) | `SERVE_PROFILE=prod SERVE_MODEL=minimax-m2.5` |
| experiment — SGLang (A10G) | `SERVE_PROFILE=experiment` |

The status badge polls `GET /api/serve/status` every 30 seconds and shows the current Modal
app state. Clicking **Deploy** calls `POST /api/serve/deploy` and triggers a background
`modal deploy` — the button disables briefly while it starts.

## BFF API reference

The dashboard is a Backend-for-Frontend: all Modal and SQLite concerns stay server-side.

### Runs

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/runs` | List all runs from SQLite, newest first (CLI + dashboard) |
| `POST` | `/api/runs` | Start a new run, returns `{"run_id": "..."}` |
| `GET` | `/api/runs/{id}` | Single run metadata (SQLite + live phase if active) |
| `DELETE` | `/api/runs/{id}` | Cancel a run (best-effort) |
| `GET` | `/api/runs/{id}/stream` | SSE stream of lifecycle events (dashboard runs only) |
| `GET` | `/api/runs/{id}/events` | Past log events from SQLite (replay on page load) |

### Tokens

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/tokens` | Runs with token data, sorted by `total_tokens` desc |

**Query parameters for `GET /api/tokens`:**

| Param | Example | Description |
|---|---|---|
| `backend` | `opencode` | Filter by backend name |
| `date_from` | `2026-05-01` | Lower bound on `started_at` (inclusive) |
| `date_to` | `2026-05-03` | Upper bound on `started_at` (inclusive) |

**Response row shape:**

```json
{
  "run_id":            "run-20260503-120000-abc123",
  "repo":              "https://github.com/org/repo",
  "task":              "Fix the login bug",
  "backend":           "opencode",
  "started_at":        "2026-05-03T12:00:00+00:00",
  "outcome":           "success",
  "prompt_tokens":     12345,
  "completion_tokens": 678,
  "total_tokens":      13023,
  "duration_s":        142.3
}
```

**`POST /api/runs` body:**

```json
{
  "repo":            "https://github.com/org/myapp",
  "task":            "Fix the off-by-one error in pagination",
  "backend":         "aider",
  "base_branch":     "main",
  "create_pr":       true,
  "run_tests":       true,
  "timeout_seconds": 300
}
```

**SSE event types** (from `GET /api/runs/{id}/stream`):

| `type` | Payload fields | Meaning |
|---|---|---|
| `phase` | `phase: str` | Phase transition (BOOTING, CLONING, …) |
| `log` | `text: str` | Agent output line |
| `done` | `success: bool`, `pr_url?`, `diff_stat?`, `error?` | Run finished |

### Serve

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/serve/status` | Modal app list for `agent-container-serve*` apps |
| `POST` | `/api/serve/deploy` | Trigger a background `modal deploy` |

**`POST /api/serve/deploy` body:**

```json
{
  "profile": "prod",
  "model":   "minimax-m2.5"
}
```

`model` is optional — only used when `profile` is `prod`. Omit it to use the default
(`qwen3-coder`).

## Architecture

```
Browser
  ← SSE stream   (phase changes + log lines, dashboard runs only)
  → REST API     (start run, list runs, cancel, serve control)

FastAPI app (dashboard/router.py)
  RunStore        — reads SQLite at ~/.agent-container/runs.db (all runs, CLI + dashboard)
  WorkspaceStore  — in-memory SSE event buffer (active dashboard runs only)
  ModalSandbox    — fires off runs in a thread pool, pushes events to WorkspaceStore
```

`RunStore` (SQLite) is the source of truth for the run list — it persists across server
restarts and captures CLI runs. `WorkspaceStore` is a lightweight in-memory ring buffer used
only for streaming live events to the browser; it is not persisted.

Dashboard runs write to both stores with the same pre-allocated `run_id`. CLI runs only write
to SQLite (no SSE available).

## Expose on LAN / team network

```bash
DASHBOARD_HOST=0.0.0.0   # default: 127.0.0.1
DASHBOARD_PORT=8080
```

!!! warning
    The dashboard has no authentication. Run it on a trusted network or behind a reverse proxy
    with auth if exposing to a team.
