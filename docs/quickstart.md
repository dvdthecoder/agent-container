# Quickstart

Get your first agent task running in under 10 minutes.

## Prerequisites

- Python 3.11+
- A [Modal](https://modal.com) account (free tier works)
- A GitHub personal access token — Contents (read) + Pull Requests (read/write)
- A HuggingFace token — read access, from [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)

## 1. Install

```bash
pip install agent-container
modal token new   # browser prompt — saves token to ~/.modal.toml
```

## 2. Deploy your model

Everything runs on Modal — including the model. Deploy it once and it scales to zero when idle.

```bash
modal deploy modal/serve.py                        # Qwen2.5-Coder 7B  — A10G        (start here)
SERVE_PROFILE=prod    modal deploy modal/serve.py  # Qwen3-Coder 80B   — 2×A100 80GB (production)
SERVE_PROFILE=minimax modal deploy modal/serve.py  # MiniMax M2.5      — 8×A100 80GB (best quality)
```

Modal prints the endpoint URL after deploy — you need it in the next step:

```
✓ Created web endpoint: https://your-org--agent-container-serve.modal.run
```

## 3. Configure

```bash
cp .env.example .env
```

Fill in four things:

```bash
# Modal credentials
MODAL_TOKEN_ID=ak-...
MODAL_TOKEN_SECRET=as-...

# HuggingFace — needed to download model weights during deploy
HF_TOKEN=hf_...

# Your model endpoint (URL from step 2 — no /v1 suffix)
OPENAI_BASE_URL=https://your-org--agent-container-serve.modal.run
OPENAI_API_KEY=modal
OPENCODE_MODEL=qwen2.5-coder   # must match SERVED_MODEL_NAME in modal/serve.py

# GitHub token — Contents (read) + Pull Requests (read/write)
GITHUB_TOKEN=ghp_...
```

## 4. Run your first task

```bash
agent-run run \
  --repo https://github.com/org/myapp \
  --task "Fix the off-by-one error in the pagination helper"
```

Output:

```
[sandbox] phase=WARMING   inference endpoint ready  elapsed=87s
[sandbox] phase=BOOTING   starting Modal sandbox...
[sandbox] phase=CLONING   git clone https://github.com/org/myapp
[sandbox] phase=RUNNING   [aider] writing changes...
[sandbox] phase=TESTING   pytest — 12 passed
[sandbox] phase=PR        opening pull request...
[sandbox] container terminated

Done in 1m 52s
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

## Smoke test

Once configured, verify the full pipeline end-to-end against the fixture repo:

```bash
make example                    # aider backend (default)
make example BACKEND=opencode   # opencode backend
# boots sandbox → clones fixture repo → runs agent → opens PR
```

---

## Next steps

- [Model Setup](models.md) — GPU profiles, vLLM, scale-to-zero
- [Agent Backends](agents.md) — use Claude Code or Gemini CLI instead of OpenCode
- [MCP Integration](mcp.md) — trigger runs from inside Claude Code
- [Enterprise & GitLab](enterprise.md) — GitLab MRs, air-gap setup
