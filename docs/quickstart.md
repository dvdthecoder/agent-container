# Quickstart

Get your first agent task running in under 5 minutes.

## Prerequisites

- Python 3.11+
- A [Modal](https://modal.com) account (free tier works)
- A GitHub personal access token
- A MiniMax API key — get one at [platform.minimax.io](https://platform.minimax.io) (recommended)
  or any other OpenAI-compatible model endpoint

## 1. Install

```bash
pip install agent-container
```

## 2. Get a Modal token

```bash
pip install modal
modal token new
# follow the browser prompt — saves token to ~/.modal.toml
```

## 3. Configure

```bash
cp .env.example .env
```

Edit `.env` — the minimum required fields:

```bash
# Modal — sandbox compute
MODAL_TOKEN_ID=ak-...
MODAL_TOKEN_SECRET=as-...

# GitHub
GITHUB_TOKEN=ghp_...

# Model — MiniMax M2.5 hosted API (recommended, no GPU setup)
OPENAI_BASE_URL=https://api.minimax.io/v1
OPENAI_API_KEY=your-minimax-api-key
OPENCODE_MODEL=MiniMax-M2.5
```

!!! tip "Self-hosted model (optional)"
    To run the model on your own Modal GPU instead:
    ```bash
    modal deploy modal/serve.py            # Qwen3-Coder 8B (test)
    SERVE_PROFILE=prod modal deploy ...    # Qwen3-Coder 80B
    SERVE_PROFILE=minimax modal deploy ... # MiniMax M2.5 on 8× A100
    ```
    Then set `OPENAI_BASE_URL` to the printed endpoint URL.

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
[pr]      opening PR on branch agent/opencode-20260424-143022
✓ Done in 1m 52s
PR: https://github.com/org/myapp/pull/42   +12 −3
```

## 5. Start the dashboard (optional)

```bash
make dashboard
# → http://localhost:8000
```

Live view of all running, completed, and failed agent runs — phases, log stream, PR links.

## 6. Wire up MCP (optional)

`.claude/settings.json` and `.gemini/settings.json` are already checked into the repo.
Fill in your tokens and the `sandbox_run`, `sandbox_list`, `sandbox_status`, `sandbox_stop`
tools appear automatically in Claude Code and Gemini CLI.

```bash
make mcp      # start MCP server standalone (stdio)
claude mcp list   # verify agent-container appears
```

---

## Next steps

- [Model Setup](models.md) — MiniMax hosted vs self-hosted, all profiles
- [Agent Backends](agents.md) — use Claude Code or Gemini CLI instead of OpenCode
- [MCP Integration](mcp.md) — trigger runs from inside Claude Code
- [Enterprise & GitLab](enterprise.md) — GitLab MRs, air-gap setup
