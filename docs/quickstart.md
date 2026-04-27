# Quickstart

Get your first agent task running in under 10 minutes.

## Prerequisites

- Python 3.11+
- A [Modal](https://modal.com) account (free tier works)
- A GitHub personal access token

## 1. Install

```bash
pip install agent-container
modal token new   # browser prompt — saves token to ~/.modal.toml
```

## 2. Deploy your model

Everything runs on Modal — including the model. Deploy it once and it scales to zero when idle.

```bash
modal deploy modal/serve.py                        # Qwen3-Coder 8B  — A10G        (start here)
SERVE_PROFILE=prod    modal deploy modal/serve.py  # Qwen3-Coder 80B — 2×A100 80GB (production)
SERVE_PROFILE=minimax modal deploy modal/serve.py  # MiniMax M2.5    — 8×A100 80GB (best quality)
```

Modal prints the endpoint URL after deploy — you need it in the next step:

```
✓ Created web endpoint: https://your-org--agent-container-serve.modal.run
```

## 3. Configure

```bash
cp .env.example .env
```

Fill in three things:

```bash
# Modal credentials
MODAL_TOKEN_ID=ak-...
MODAL_TOKEN_SECRET=as-...

# Your model endpoint (URL from step 2)
OPENAI_BASE_URL=https://your-org--agent-container-serve.modal.run/v1
OPENAI_API_KEY=modal
OPENCODE_MODEL=qwen3-coder   # or minimax-m2.5 — see modal/serve.py

# GitHub token — Contents (read) + Pull Requests (read/write)
GITHUB_TOKEN=ghp_...
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

`.claude/settings.json` is already checked into the repo. Fill in your tokens and the
`sandbox_run`, `sandbox_list`, `sandbox_status`, `sandbox_stop` tools appear automatically
in Claude Code and Gemini CLI.

```bash
make mcp          # start MCP server standalone (stdio)
claude mcp list   # verify agent-container appears
```

---

## Next steps

- [Model Setup](models.md) — GPU profiles, SGLang, RadixAttention caching
- [Agent Backends](agents.md) — use Claude Code or Gemini CLI instead of OpenCode
- [MCP Integration](mcp.md) — trigger runs from inside Claude Code
- [Enterprise & GitLab](enterprise.md) — GitLab MRs, air-gap setup
