# agent-container

A secure, ephemeral sandbox for autonomous coding agents. Give it a task and a repo — it boots a
fresh container on [Modal](https://modal.com), runs an AI coding agent inside it, opens a PR, and
destroys the container. Nothing persists. Nothing leaks. No Docker required on your machine.

---

## What it does

```
$ agent-run run \
    --repo https://github.com/org/myapp \
    --task "Fix the off-by-one error in pagination" \
    --backend aider

  [sandbox] phase=WARMING   inference endpoint ready  elapsed=94s
  [sandbox] phase=BOOTING   starting Modal sandbox...
  [sandbox] phase=CLONING   git clone https://github.com/org/myapp
  [sandbox] phase=RUNNING   [aider] writing changes...
  [sandbox] phase=TESTING   pytest — 12 passed
  [sandbox] phase=PR        opening pull request...
  [sandbox] container terminated

  Done in 148s
  PR: https://github.com/org/myapp/pull/42   +67 −3
```

The agent never touches your local machine. The sandbox boots, does the work, opens the PR, and
is destroyed — all on Modal.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  agent-run CLI  /  Dashboard (localhost:8000)                   │
│  Python API     /  MCP server (Claude Code, Gemini CLI)         │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Modal — agent sandbox (ephemeral container, one per run)       │
│                                                                 │
│  WARMING → BOOTING → CLONING → RUNNING → TESTING → PR          │
│                                                                 │
│  Backends:                                                      │
│    aider      — Chat Completions direct, whole-file edit format │
│    opencode   — Responses API proxy → Chat Completions adapter  │
│    claude     — Claude Code CLI (Anthropic API)                 │
│    gemini     — Gemini CLI (Google AI)                          │
└──────────────────────────────┬──────────────────────────────────┘
                               │  POST /v1/chat/completions
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Modal — model serving (modal/serve.py)                         │
│  vLLM + Qwen2.5-Coder / Qwen3-Coder / MiniMax M2.5             │
│  Scale-to-zero. Weights cached in Modal Volume.                 │
└─────────────────────────────────────────────────────────────────┘
```

**Everything runs on Modal.** The agent sandbox and the model endpoint are separate Modal resources.
The sandbox calls the inference server over the network. No external API keys required for
self-hosted backends.

---

## Prerequisites

Before running `modal deploy`, make sure these four things are in your `.env`:

| Variable | Where to get it | Why |
|---|---|---|
| `HF_TOKEN` | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) — read access | `modal/serve.py` reads this at deploy time to download model weights |
| `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` | [modal.com/settings/tokens](https://modal.com/settings/tokens) | authenticates the Modal CLI |
| `GITHUB_TOKEN` | GitHub → Settings → Developer settings → Fine-grained tokens | opens pull requests from the sandbox |
| `OPENAI_BASE_URL` | printed by `modal deploy` | points the sandbox at your model endpoint |

```bash
cp .env.example .env
# fill in HF_TOKEN, MODAL_TOKEN_ID, MODAL_TOKEN_SECRET, GITHUB_TOKEN
# then:
modal token new          # browser login, saves to ~/.modal.toml
modal deploy modal/serve.py   # prints OPENAI_BASE_URL — paste it into .env
```

> **If `modal deploy` fails with `KeyError: 'HF_TOKEN'`** — your `.env` is missing the token.
> Get one at huggingface.co/settings/tokens (read scope is enough for all supported models).

---

## Documentation

**[dvdthecoder.github.io/agent-container](https://dvdthecoder.github.io/agent-container)**

[Quickstart](https://dvdthecoder.github.io/agent-container/quickstart) ·
[Models](https://dvdthecoder.github.io/agent-container/models) ·
[Backends](https://dvdthecoder.github.io/agent-container/agents) ·
[Dashboard](https://dvdthecoder.github.io/agent-container/dashboard) ·
[MCP](https://dvdthecoder.github.io/agent-container/mcp) ·
[Analysis](https://dvdthecoder.github.io/agent-container/analysis/) ·
[Lessons learned](https://dvdthecoder.github.io/agent-container/lessons-learned)
