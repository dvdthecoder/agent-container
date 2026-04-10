# Quickstart

Get your first agent task running in under 5 minutes.

## Prerequisites

- Python 3.11+
- A [Modal](https://modal.com) account (free tier works)
- A GitHub personal access token
- A model endpoint (see options below — Together.ai is easiest)

## 1. Install

```bash
pip install agent-container
```

## 2. Get a Modal token

```bash
pip install modal
modal token new
# follow the browser prompt — sets MODAL_TOKEN_ID and MODAL_TOKEN_SECRET in ~/.modal.toml
```

## 3. Configure

```bash
cp .env.example .env
```

Edit `.env`:

```bash
# Modal
MODAL_TOKEN_ID=ak-...
MODAL_TOKEN_SECRET=as-...

# GitHub
GITHUB_TOKEN=ghp_...

# Model — Together.ai is the quickest path (free trial credits available)
OPENAI_BASE_URL=https://api.together.xyz/v1
OPENAI_API_KEY=your-together-key
OPENCODE_MODEL=Qwen/Qwen3-Coder-80B-Instruct
```

## 4. Run your first task

```bash
agent-run \
  --repo https://github.com/org/myapp \
  --task "Fix the off-by-one error in the pagination helper"
```

Output:

```
[sandbox] booting Modal container...
[clone]   git clone https://github.com/org/myapp
[agent]   running opencode...
[pr]      opening PR on branch agent/fix-pagination-20260410-143022
✓ Done in 1m 52s
PR: https://github.com/org/myapp/pull/42   +12 −3
```

## 5. Start the dashboard (optional)

```bash
agent-run dashboard
```

Open [http://localhost:8080](http://localhost:8080) to see live run status, logs, and history.

---

## Next steps

- [Model Setup](models.md) — switch to a self-hosted or private model
- [Agent Backends](agents.md) — use Claude Code or Gemini CLI instead of OpenCode
- [MCP Integration](mcp.md) — trigger runs from inside Claude Code
- [Enterprise & GitLab](enterprise.md) — GitLab MRs, air-gap setup
