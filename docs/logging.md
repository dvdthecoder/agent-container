# Run Logs

Every agent run is recorded to a local SQLite database at `~/.agent-container/runs.db`.
Nothing is sent anywhere — it is a purely local debugging artefact.

Two tables are written:

- **`runs`** — one row per run: repo, task, backend, outcome, branch, PR URL, duration, sandbox ID
- **`events`** — one row per log line: timestamp, elapsed time, pipeline phase, source, level, message

---

## Querying from the CLI

### List recent runs

```bash
agent-run logs
```

```
RUN ID                            STARTED                   OUTCOME    DUR    REPO
--------------------------------  ------------------------  ---------  -----  --------------------------------
run-20260430-143022-abc123        2026-04-30 14:30:22       success    148.3s dvdthecoder/agent-container-fixture
run-20260430-131005-def456        2026-04-30 13:10:05       failure    42.1s  org/myapp
run-20260430-120011-ghi789        2026-04-30 12:00:11       success    201.7s org/myapp
```

Show more rows:

```bash
agent-run logs -n 50
```

### Inspect a specific run

```bash
agent-run logs run-20260430-143022-abc123
```

```
run_id   : run-20260430-143022-abc123
repo     : https://github.com/dvdthecoder/agent-container-fixture
task     : update hello_world to return the string 'Hello, World! run-abc123'
backend  : aider
started  : 2026-04-30T14:30:22.000000+00:00
outcome  : success  (148.3s)
pr       : https://github.com/dvdthecoder/agent-container-fixture/pull/7
sandbox  : sb-abc123

[14:30:22.001]    0.00s  WARMING   runner               INFO  phase=WARMING
[14:31:56.441]   94.44s  WARMING   runner               INFO  inference endpoint ready
[14:31:56.500]   94.50s  BOOTING   runner               INFO  phase=BOOTING
[14:31:59.123]   97.12s  CLONING   runner               INFO  phase=CLONING
[14:32:01.400]   99.40s  RUNNING   runner               INFO  phase=RUNNING
[14:32:38.900]  136.90s  RUNNING   aider                INFO  Applied edit to greet.py
[14:32:40.200]  138.20s  TESTING   runner               INFO  phase=TESTING
[14:32:42.500]  140.50s  TESTING   tester               INFO  pytest — 3 passed
[14:32:43.100]  141.10s  PR        runner               INFO  phase=PR
[14:32:46.300]  144.30s  PR        runner               INFO  PR opened: .../pull/7
```

### Filter events

```bash
# Errors only
agent-run logs <run-id> --level error

# Output from the agent phase only
agent-run logs <run-id> --phase RUNNING

# A specific source (e.g. aider output, tester, runner)
agent-run logs <run-id> --source aider
agent-run logs <run-id> --source tester

# Combine filters
agent-run logs <run-id> --phase RUNNING --level warn
```

### Use a non-default database path

```bash
agent-run logs --db /path/to/runs.db
agent-run logs <run-id> --db /path/to/runs.db
```

---

## Querying from Python

```python
from agent.log_store import RunStore

store = RunStore()  # reads ~/.agent-container/runs.db by default

# List recent runs
runs = store.list_runs(limit=20)
for r in runs:
    print(r.run_id, r.outcome, r.pr_url)

# Get a single run
run = store.get_run("run-20260430-143022-abc123")
print(run.duration_s, run.branch)

# All events for a run
events = store.events("run-20260430-143022-abc123")

# Filtered events
errors = store.events(run_id, level="error")
running_phase = store.events(run_id, phase="RUNNING")
aider_output = store.events(run_id, source="aider")
```

---

## Schema reference

### `runs` table

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | TEXT | Unique ID — format `run-YYYYMMDD-HHMMSS-<hex6>` |
| `repo` | TEXT | Full repo URL passed to `--repo` |
| `task` | TEXT | Task string |
| `backend` | TEXT | `aider` / `opencode` / `claude` / `gemini` |
| `started_at` | TEXT | ISO 8601 UTC timestamp |
| `finished_at` | TEXT | ISO 8601 UTC timestamp — null if still running |
| `outcome` | TEXT | `success` / `failure` — null if still running |
| `branch` | TEXT | Agent branch name, e.g. `agent/aider-20260430-143022` |
| `pr_url` | TEXT | PR or MR URL — null if no PR was created |
| `duration_s` | REAL | Wall-clock seconds |
| `sandbox_id` | TEXT | Modal sandbox container ID |

### `events` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Auto-increment row ID |
| `run_id` | TEXT | Foreign key → `runs.run_id` |
| `ts` | TEXT | ISO 8601 UTC timestamp |
| `elapsed_s` | REAL | Seconds since run started |
| `phase` | TEXT | Pipeline phase: `WARMING` / `BOOTING` / `CLONING` / `RUNNING` / `TESTING` / `PR` |
| `source` | TEXT | Emitting component: `runner` / `aider` / `tester` / `sandbox:stderr` |
| `level` | TEXT | `info` / `warn` / `error` |
| `message` | TEXT | Log line content |

---

## Direct SQL access

The database is a plain SQLite file — open it with any SQLite client for ad-hoc queries:

```bash
sqlite3 ~/.agent-container/runs.db
```

```sql
-- All successful runs with a PR
SELECT run_id, repo, duration_s, pr_url
FROM runs
WHERE outcome = 'success' AND pr_url IS NOT NULL
ORDER BY started_at DESC;

-- Average run duration by backend
SELECT backend, ROUND(AVG(duration_s), 1) AS avg_s, COUNT(*) AS runs
FROM runs
WHERE outcome IS NOT NULL
GROUP BY backend;

-- All errors across recent runs
SELECT r.run_id, r.repo, e.elapsed_s, e.phase, e.message
FROM events e
JOIN runs r ON e.run_id = r.run_id
WHERE e.level = 'error'
ORDER BY e.ts DESC
LIMIT 50;

-- Slowest phase per run
SELECT run_id,
       phase,
       MAX(elapsed_s) - MIN(elapsed_s) AS phase_duration_s
FROM events
GROUP BY run_id, phase
ORDER BY phase_duration_s DESC
LIMIT 20;
```
