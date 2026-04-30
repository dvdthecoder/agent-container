# CLI Reference

## `agent-run`

Run an agent task in an ephemeral Modal sandbox.

```
agent-run [OPTIONS]
```

### Options

| Flag | Default | Description |
|---|---|---|
| `--repo URL` | required | Git repo URL (`https://` or `git@`) |
| `--task TEXT` | required* | Task description |
| `--task-file PATH` | required* | Read task from a Markdown file |
| `--backend` | `aider` | `aider` \| `opencode` \| `claude` \| `gemini` \| `stub` |
| `--branch` | `main` | Base branch to clone and branch from |
| `--image` | from config | Docker image override |
| `--timeout` | `300` | Seconds before the run is killed |
| `--no-pr` | — | Skip PR creation (diff only) |

*Exactly one of `--task` or `--task-file` is required.

### Examples

```bash
# inline task
agent-run --repo https://github.com/org/myapp \
          --task "Fix the off-by-one in paginate()"

# task from file (good for long, structured prompts)
agent-run --repo https://github.com/org/myapp \
          --task-file tasks/add-rate-limiting.md

# use Claude Code backend
agent-run --repo https://github.com/org/myapp \
          --task "Refactor auth middleware" \
          --backend claude

# diff only, no PR
agent-run --repo https://github.com/org/myapp \
          --task "Add type hints to utils.py" \
          --no-pr

# GitLab
agent-run --repo https://gitlab.yourcompany.com/org/myapp \
          --task "Security patch: upgrade requests to 2.32"
```

### Output

- **stderr**: human-readable phase updates and log lines
- **stdout**: `AgentTaskResult` as JSON (pipe-friendly)
- **exit code**: `0` on success, `1` on failure

```bash
# capture result for scripting
result=$(agent-run --repo ... --task ... 2>/dev/null)
pr_url=$(echo "$result" | python -c "import sys,json; print(json.load(sys.stdin)['pr_url'])")
```

---

## `agent-run logs`

Inspect run history stored in `~/.agent-container/runs.db`.

```
agent-run logs [RUN_ID] [OPTIONS]
```

| Flag | Default | Description |
|---|---|---|
| `RUN_ID` | — | Show events for this run (omit to list recent runs) |
| `--level` | — | Filter events by level: `info` \| `warn` \| `error` |
| `--phase` | — | Filter events by phase: `WARMING` \| `BOOTING` \| `CLONING` \| `RUNNING` \| `TESTING` \| `PR` |
| `--source` | — | Filter events by source: `runner` \| `aider` \| `tester` \| `sandbox:stderr` |
| `-n, --limit` | `20` | Number of runs to show in list view |
| `--db PATH` | `~/.agent-container/runs.db` | Path to a non-default database |

### Examples

```bash
# List recent runs
agent-run logs

# All events for a run
agent-run logs run-20260430-143022-abc123

# Errors only
agent-run logs run-20260430-143022-abc123 --level error

# Agent output only (RUNNING phase)
agent-run logs run-20260430-143022-abc123 --phase RUNNING

# Show 50 most recent runs
agent-run logs -n 50
```

See [Run Logs](logging.md) for full querying documentation including direct SQL access.

---

## `agent-run dashboard`

Start the web dashboard.

```
agent-run dashboard [--host HOST] [--port PORT]
```

| Flag | Default | Description |
|---|---|---|
| `--host` | `127.0.0.1` | Bind address |
| `--port` | `8080` | Port |

---

## Environment variables

All configuration is via environment variables (loaded from `.env` if present).

| Variable | Required | Default | Description |
|---|---|---|---|
| `MODAL_TOKEN_ID` | ✅ | — | Modal authentication |
| `MODAL_TOKEN_SECRET` | ✅ | — | Modal authentication |
| `GITHUB_TOKEN` | ✅ | — | For PR creation |
| `GITLAB_TOKEN` | — | — | For MR creation (GitLab) |
| `GITLAB_URL` | — | gitlab.com | Self-hosted GitLab URL |
| `OPENAI_BASE_URL` | ✅ | — | Model endpoint |
| `OPENAI_API_KEY` | ✅ | — | Model API key |
| `OPENCODE_MODEL` | ✅ | — | Model identifier |
| `ANTHROPIC_API_KEY` | — | — | Required for `--backend claude` |
| `GEMINI_API_KEY` | — | — | Required for `--backend gemini` |
| `AGENT_DEFAULT_IMAGE` | — | `mcr.microsoft.com/devcontainers/base:ubuntu-24.04` | Container image |
| `AGENT_WORKSPACE_TIMEOUT` | — | `300` | Default timeout in seconds |
| `AGENT_BACKEND` | — | `aider` | Default backend |
| `DASHBOARD_HOST` | — | `127.0.0.1` | Dashboard bind address |
| `DASHBOARD_PORT` | — | `8080` | Dashboard port |
