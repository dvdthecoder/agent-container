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

## 2. Configure `.env` before deploying

Copy the example file and fill in your tokens **before** running `modal deploy`.
`modal/serve.py` reads `HF_TOKEN` from `.env` at deploy time — if it is missing the deploy
fails with `KeyError: 'HF_TOKEN'`.

```bash
cp .env.example .env
```

Minimum required fields:

```bash
# HuggingFace — read access is enough for all supported models.
# Get a token at https://huggingface.co/settings/tokens
HF_TOKEN=hf_...

# Modal CLI credentials — get at https://modal.com/settings/tokens
MODAL_TOKEN_ID=ak-...
MODAL_TOKEN_SECRET=as-...

# GitHub — fine-grained token: Contents (read) + Pull Requests (read/write)
GITHUB_TOKEN=ghp_...
```

!!! warning "HF_TOKEN must exist before `modal deploy`"
    `modal/serve.py` bakes `HF_TOKEN` into the container secret at deploy time.
    If it is missing you will see `KeyError: 'HF_TOKEN'` and the deploy will abort.
    Gated models (Qwen3-Coder, MiniMax M2.5) also require that your HuggingFace
    account has been granted access on each model's HuggingFace page before downloading.

## 3. Deploy your model

Everything runs on Modal — including the model. Deploy it once and it scales to zero when idle.

```bash
modal deploy modal/serve.py   # default: Qwen2.5-Coder 32B · A100 80GB
```

Modal prints the endpoint URL after deploy — copy it:

```
✓ Created web endpoint: https://your-org--agent-container-serve-qwen2-5-coder-32b-serve.modal.run
```

Add it to `.env`:

```bash
OPENAI_BASE_URL=https://your-org--agent-container-serve-qwen2-5-coder-32b-serve.modal.run
OPENAI_API_KEY=modal
OPENCODE_MODEL=qwen2.5-coder-32b   # must match served_name in modal/serve.py
```

See [Model Profile Guide](model-profiles.md) for other model options and GPU sizing.

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

- [Model Profile Guide](model-profiles.md) — when to use 7B vs 32B vs MoE, GPU sizing
- [Model Setup](models.md) — GPU profiles, vLLM, scale-to-zero
- [Agent Backends](agents.md) — use Claude Code or Gemini CLI instead of OpenCode
- [MCP Integration](mcp.md) — trigger runs from inside Claude Code
- [Enterprise & GitLab](enterprise.md) — GitLab MRs, air-gap setup
