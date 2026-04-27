# Model Setup

The model runs on Modal GPU via `modal/serve.py` — the same platform that runs the agent sandbox.
Deploy once, it scales to zero when idle, weights are cached so cold starts after the first are fast.

Three env vars tell the agent sandbox where the model is:

```bash
OPENAI_BASE_URL=https://your-org--agent-container-serve.modal.run/v1
OPENAI_API_KEY=modal        # any non-empty string — it's your own endpoint
OPENCODE_MODEL=qwen3-coder  # must match SERVED_MODEL_NAME in modal/serve.py
```

---

## GPU profiles

Three profiles are built into `modal/serve.py`. Pick one based on your quality and cost needs:

| Profile | Model | GPU | Context | Deploy command |
|---|---|---|---|---|
| `test` (default) | Qwen3-Coder 8B | A10G | 32k | `modal deploy modal/serve.py` |
| `prod` | Qwen3-Coder 80B | 2× A100 80GB | 128k | `SERVE_PROFILE=prod modal deploy modal/serve.py` |
| `minimax` | MiniMax M2.5 | 8× A100 80GB | 1M | `SERVE_PROFILE=minimax modal deploy modal/serve.py` |

**Start with `test`** — it deploys in ~30 seconds, cold starts in ~30s, and is cheap enough for
iterating. Promote to `prod` or `minimax` when you want production-grade output.

### Model names

After deploy, set `OPENCODE_MODEL` to match the `SERVED_MODEL_NAME` in `modal/serve.py`:

| Profile | `OPENCODE_MODEL` |
|---|---|
| `test` / `prod` | `qwen3-coder` |
| `minimax` | `minimax-m2.5` |

---

## How it works

```
┌─────────────────────────────────────────────────┐
│  Modal                                          │
│                                                 │
│  Agent sandbox          Model server            │
│  ┌──────────────┐       ┌─────────────────┐     │
│  │ opencode     │──────▶│ SGLang          │     │
│  │ (your task)  │ HTTP  │ Qwen3-Coder     │     │
│  └──────────────┘       │ or MiniMax M2.5 │     │
│                         └─────────────────┘     │
└─────────────────────────────────────────────────┘
```

The sandbox and the model server communicate over Modal's internal network. No traffic leaves Modal.

---

## Why SGLang

SGLang's **RadixAttention** automatically caches shared KV prefixes across requests. Agent runs
against the same repo re-use the cached system prompt and repo context — only task-specific tokens
are computed fresh.

This matters for agent-container because:

- Every run against the same repo shares a large common prefix (system prompt + file tree)
- Multiple CI runs against the same repo in parallel all hit the prefix cache

Benchmark: `make test-e2e` covers end-to-end wall time. Issue #48 tracks a formal throughput
comparison at 1/4/8 concurrent runs.

---

## Scale-to-zero and cold starts

Modal scales the model server to zero after `scaledown_window` seconds of inactivity (5–10 min
depending on profile). You pay only for active inference time.

Cold start times (first request after scale-down):

| Profile | Cold start |
|---|---|
| `test` | ~30s |
| `prod` | ~2–3 min |
| `minimax` | ~5–8 min |

Model weights are stored in a Modal Volume (`agent-container-models`) and are not re-downloaded
on cold start after the first deploy.
