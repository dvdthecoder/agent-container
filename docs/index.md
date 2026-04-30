# agent-container

**Secure, ephemeral sandbox for autonomous coding agents.**

Give it a task and a repo — it boots a fresh container on [Modal](https://modal.com), runs an AI
coding agent inside it, opens a PR, and destroys the container. Nothing persists. Nothing leaks.
No Docker required on your machine.

```
$ agent-run run \
    --repo https://github.com/org/myapp \
    --task "Add rate limiting to /api/login — max 5 requests/min per IP" \
    --backend aider

  [sandbox] phase=WARMING   inference endpoint ready  elapsed=87s
  [sandbox] phase=BOOTING   starting Modal sandbox...
  [sandbox] phase=CLONING   git clone https://github.com/org/myapp
  [sandbox] phase=RUNNING   [aider] writing changes...
  [sandbox] phase=TESTING   pytest — 14 passed
  [sandbox] phase=PR        opening pull request...
  [sandbox] container terminated

  Done in 2m 14s
  PR: https://github.com/org/myapp/pull/42   +67 −3
```

---

## Why this exists

Running an AI coding agent directly on your laptop or CI machine is a bad idea:

- The agent has shell access, can read env vars, write arbitrary files
- Mistakes persist — a bad edit is a bad edit until someone reverts it
- Concurrent runs conflict on the same filesystem
- There's no audit trail of what the agent actually did

`agent-container` solves this by running every agent task in a fresh, isolated container that is
destroyed when the task ends. The agent proposes a change (as a PR). A human decides whether to
merge it.

---

## How it fits together

```
Your terminal / dashboard / Claude Code session
        ↓
  agent-run CLI  (or Python API, or MCP tool)
        ↓
  Modal Sandbox  ← ephemeral container, destroyed after each run
  WARMING → BOOTING → CLONING → RUNNING → TESTING → PR
        ↓
  Coding agent   ← aider / opencode / Claude Code CLI / Gemini CLI
        ↓
  Model endpoint ← vLLM on Modal GPU (or Anthropic / Google AI)
        ↓
  GitHub / GitLab PR
```

See [Architecture](architecture.md) for the full picture.

---

## When to use it

| Scenario | Fit |
|---|---|
| Fix a bug from a GitHub issue automatically | ✅ |
| Apply a security patch across 50 microservices | ✅ |
| Nightly dependency upgrades via scheduled runs | ✅ |
| Interactive pair programming | ❌ — use Claude Code / Cursor directly |
| Generating entire new applications from scratch | ❌ — better with a human in the loop |

---

## Get started

[Quickstart →](quickstart.md){ .md-button .md-button--primary }
[Architecture →](architecture.md){ .md-button }
