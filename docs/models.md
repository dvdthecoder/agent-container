# Model Setup

The model runs on Modal GPU infrastructure, deployed once and called by the sandbox over Modal's
internal network.

## Deploy the model

```bash
modal deploy modal/serve.py
```

This starts Qwen3-Coder on an A100 GPU and exposes an internal Modal endpoint. The sandbox
container calls it automatically — no URL configuration needed, it's wired up inside Modal's
network.

Scale-to-zero when idle. You pay only for GPU seconds while the model is actually processing a
request.

## Why Qwen3-Coder

Qwen3-Coder 80B scores **70.6 on SWE-bench** — the standard benchmark for real code editing on
real repositories. It is purpose-built for software engineering tasks: reading large codebases,
understanding context, making targeted edits, writing tests.

It fits on a single A100 80GB, which is what `modal/serve.py` provisions.

## Why Modal for the model

- No GPU hardware to buy or manage
- Same infrastructure as the sandbox — one platform, one bill, one set of credentials
- Internal network between sandbox and model — no public internet hop, lower latency
- Scale-to-zero: costs nothing when no agent runs are happening

## Agent backends and model coupling

| Backend | Model |
|---|---|
| `opencode` | Qwen3-Coder via Modal (default) |
| `claude` | Anthropic API (Claude Code CLI reads `ANTHROPIC_API_KEY`) |
| `gemini` | Google AI / Vertex (Gemini CLI reads `GEMINI_API_KEY`) |

The `opencode` backend is the default and recommended path — it uses the Modal-hosted model,
keeping everything within Modal's infrastructure.

The `claude` and `gemini` backends are available for teams already standardised on those CLIs.
When using them, prompts go to Anthropic or Google respectively.
