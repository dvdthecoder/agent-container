# Model Setup

The model runs on Modal GPU via `modal/serve.py`. Deploy once, it scales to zero when idle,
weights are cached so cold starts after the first are fast.

Three env vars tell the agent sandbox where the model is:

```bash
OPENAI_BASE_URL=https://your-org--agent-container-serve-serve.modal.run
OPENAI_API_KEY=modal        # any non-empty string — it's your own endpoint
OPENCODE_MODEL=qwen2.5-coder  # must match SERVED_MODEL_NAME in modal/serve.py
```

!!! note "No /v1 suffix"
    Set `OPENAI_BASE_URL` without a trailing `/v1`. The inference server and adapter add it.

---

## GPU profiles

Three profiles are built into `modal/serve.py`. Pick one based on your quality and cost needs:

| Profile | Model | GPU | Context | Deploy command |
|---------|-------|-----|---------|----------------|
| `test` (default) | Qwen2.5-Coder-7B | A10G | 32k | `modal deploy modal/serve.py` |
| `prod` | Qwen3-Coder-80B | 2× A100 80GB | 128k | `SERVE_PROFILE=prod modal deploy modal/serve.py` |
| `minimax` | MiniMax-M2.5 | 8× A100 80GB | 1M | `SERVE_PROFILE=minimax modal deploy modal/serve.py` |

**Start with `test`** — cheap, ~30s cold start, good for iterating. Promote to `prod` or `minimax`
when you want production-grade output quality.

### Model names

Set `OPENCODE_MODEL` to match `SERVED_MODEL_NAME` in `modal/serve.py`:

| Profile | `OPENCODE_MODEL` |
|---------|-----------------|
| `test` | `qwen2.5-coder` |
| `prod` | `qwen3-coder` |
| `minimax` | `minimax-m2.5` |

---

## How it works

```
┌─────────────────────────────────────────────────┐
│  Modal                                          │
│                                                 │
│  Agent sandbox          Model server            │
│  ┌──────────────┐       ┌─────────────────┐     │
│  │ aider        │──────▶│ vLLM            │     │
│  │ (your task)  │ HTTP  │ Qwen / MiniMax  │     │
│  └──────────────┘       └─────────────────┘     │
└─────────────────────────────────────────────────┘
```

The sandbox and the model server communicate over Modal's internal network. No traffic leaves Modal.

---

## Inference server — vLLM

[vLLM](https://github.com/vllm-project/vllm) provides a stable OpenAI-compatible
`/v1/chat/completions` API with first-class tool calling support. It serves all three model
profiles and handles:

- Tool calling natively (`--enable-auto-tool-choice --tool-call-parser`)
- Tensor parallelism for multi-GPU profiles (`--tensor-parallel-size`)
- KV prefix caching — agent runs against the same repo share cached context

### Why not SGLang?

SGLang v0.4.7 (the latest cu124 image) has blocking bugs in its tool-call parser that crash
the server on requests carrying tool schemas. vLLM is the stable primary choice; SGLang is
tracked in [issue #86](https://github.com/dvdthecoder/agent-container/issues/86) as a
secondary option pending a compatible image release.

---

## Scale-to-zero and cold starts

Modal scales the model server to zero after `scaledown_window` seconds of inactivity.
You pay only for active inference time.

| Profile | Cold start |
|---------|-----------|
| `test` | ~1–2 min |
| `prod` | ~3–5 min |
| `minimax` | ~8–12 min |

Model weights are stored in a Modal Volume (`agent-container-models`) and are not
re-downloaded on cold start after the first deploy.

The agent sandbox waits for the model to be ready before starting (preflight probe with
configurable timeout via `SERVE_COLDSTART_BUDGET`).
