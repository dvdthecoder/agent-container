# Model Setup

The sandbox passes three env vars into every container run:

```bash
OPENAI_BASE_URL=...   # any OpenAI-compatible endpoint
OPENAI_API_KEY=...    # key for that endpoint
OPENCODE_MODEL=...    # model name the endpoint accepts
```

That's the full coupling. Any OpenAI-compatible inference server works — hosted or self-hosted.

---

## Recommended: MiniMax M2.5

MiniMax M2.5 is currently the top-ranked model on [SWE-bench](https://swebench.com) — the
standard benchmark for autonomous code editing on real repositories.

| Property | Value |
|---|---|
| Architecture | MoE + Lightning Attention |
| Active params | ~45B (out of 456B total) |
| Context window | 1 000 000 tokens |
| SWE-bench score | **#1 as of 2026-04** |

### Route A — Hosted API (fastest setup, no GPU cost)

Get an API key at **platform.minimax.chat**, then set three env vars:

```bash
OPENAI_BASE_URL=https://api.minimax.chat/v1
OPENAI_API_KEY=your-minimax-api-key
OPENCODE_MODEL=MiniMax-M2.5
```

Done. No deployment step — `agent-run` calls the MiniMax API directly from inside the
sandbox container.

### Route B — Self-hosted on Modal GPU (full control, 1M context)

```bash
SERVE_PROFILE=minimax modal deploy modal/serve.py
```

Modal deploys SGLang on 8× A100 80GB with tensor parallelism. After deployment:

```bash
OPENAI_BASE_URL=https://your-org--agent-container-serve.modal.run/v1
OPENAI_API_KEY=modal
OPENCODE_MODEL=minimax-m2.5
```

!!! note "Cold start"
    The MiniMax profile uses 8× A100 80GB — cold start takes ~10 minutes on first deploy.
    Set `SCALEDOWN_WINDOW=600` (default) to keep it warm between runs.

---

## Profiles (self-hosted)

`modal/serve.py` ships with three profiles, selected via `SERVE_PROFILE`:

| Profile | Model | GPU | Context | Best for |
|---|---|---|---|---|
| `test` (default) | Qwen3-Coder 8B | A10G | 32 k | Development, CI, cheap iteration |
| `prod` | Qwen3-Coder 80B | 2× A100 80GB | 128 k | Production Qwen3 runs |
| `minimax` | MiniMax M2.5 | 8× A100 80GB | 1 M | Best quality, large repo context |

```bash
modal deploy modal/serve.py                        # test
SERVE_PROFILE=prod modal deploy modal/serve.py     # prod
SERVE_PROFILE=minimax modal deploy modal/serve.py  # minimax
```

---

## Why SGLang

SGLang's **RadixAttention** automatically caches shared KV prefixes across requests using a radix
tree. Agent runs against the same repo repeatedly re-use the cached representation of the system
prompt and repo context — only the task-specific tokens are computed from scratch.

This matters for agent-container because:

- Every run against the same repo shares a large common prefix (system prompt + file tree)
- Multiple CI runs against the same repo in parallel hit the cache simultaneously
- Per-token latency stays stable as concurrency grows

Benchmarks show ~29% higher throughput vs vLLM on prefix-heavy workloads (H100, ShareGPT-style).
For purely unique-prompt workloads the gap closes, but the agent pattern is prefix-heavy by nature.

---

## Agent backends and model coupling

| Backend | Model source |
|---|---|
| `opencode` | Any endpoint via `OPENAI_BASE_URL` — MiniMax, Qwen3, or self-hosted |
| `claude` | Anthropic API (`ANTHROPIC_API_KEY`) |
| `gemini` | Google AI / Vertex (`GEMINI_API_KEY`) |

The `opencode` backend is the default. It uses whatever endpoint `OPENAI_BASE_URL` points to —
swap the three env vars to change models with zero code changes.
