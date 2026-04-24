# Dashboard

The dashboard is a live admin view of all agent runs — running, completed, and failed.

## Start

```bash
make dashboard
# or: uvicorn dashboard.app:app --reload --port 8000
# → http://localhost:8000
```

## What you see

Each run appears as a card showing:

- Current phase with a live indicator
- Elapsed time
- Live log output (streams in real time — no page refresh)
- On completion: PR link, diff stat, test results

```
● BOOTING     starting Modal sandbox...
● CLONING     git clone https://github.com/org/myapp
● RUNNING     [opencode] Reading api/login.py...
              [opencode] Identified off-by-one in paginate()
              [opencode] Writing fix...
◉ DONE        PR #42 opened   +12 −3
```

## Run phases

| Phase | Meaning |
|---|---|
| `BOOTING` | Modal sandbox container starting |
| `CLONING` | `git clone` running inside container |
| `RUNNING` | Coding agent executing |
| `TESTING` | Test suite running (if detected) |
| `PR` | Creating branch and opening PR |
| `DONE` | Run complete, container destroyed |
| `FAILED` | Error occurred, container destroyed |

## Architecture

The dashboard is a FastAPI app with Server-Sent Events (SSE) for real-time streaming.

```
Browser
  ← SSE stream  (phase changes + log lines)
  → REST API    (start run, stop run, list runs)

FastAPI app
  WorkspaceStore  (in-memory run state + log ring buffer)
  ModalSandbox    (fires off runs, pushes events to store)
```

No WebSockets, no message broker. SSE is sufficient — it's unidirectional (server → browser),
works through proxies, and reconnects automatically.

## Expose on LAN / team network

```bash
DASHBOARD_HOST=0.0.0.0   # default: 127.0.0.1
DASHBOARD_PORT=8080
```

!!! warning
    The dashboard has no authentication. Run it on a trusted network or behind a reverse proxy
    with auth if exposing to a team.
