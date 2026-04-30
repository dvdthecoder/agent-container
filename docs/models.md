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

Four profiles are built into `modal/serve.py`:

| Profile | Engine | Model | GPU | Context | Deploy command |
|---------|--------|-------|-----|---------|----------------|
| `test` (default) | vLLM | Qwen2.5-Coder-7B | A10G | 32k | `modal deploy modal/serve.py` |
| `prod` | vLLM | Qwen3-Coder-80B | 2× A100 80GB | 128k | `SERVE_PROFILE=prod modal deploy modal/serve.py` |
| `minimax` | vLLM | MiniMax-M2.5 | 8× A100 80GB | 1M | `SERVE_PROFILE=minimax modal deploy modal/serve.py` |
| `sglang` | SGLang | Qwen2.5-Coder-7B | A10G | 32k | `SERVE_PROFILE=sglang modal deploy modal/serve.py` |

**Start with `test`** — cheap, fast iteration. Promote to `prod` or `minimax` for
production-grade output quality. Use `sglang` only for Phase 3 validation (see below).

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

### Why vLLM is the default

SGLang v0.4.7 had blocking bugs in its tool-call parser — the `--tool-call-parser qwen25` flag
crashed the server on the first request carrying tool schemas, and streaming with tools hung
indefinitely. vLLM is the stable primary choice.

### SGLang — Phase 3 validation

The `sglang` profile exists specifically to validate whether SGLang has fixed those bugs in a
newer image. It deploys to a **separate Modal app** (`agent-container-serve-sglang`) so the
vLLM endpoint is never disturbed — both can run simultaneously.

```bash
# Deploy SGLang alongside the existing vLLM endpoint
SERVE_PROFILE=sglang modal deploy modal/serve.py
# → https://your-org--agent-container-serve-sglang-serve.modal.run

# Point at the SGLang endpoint and run the opencode smoke test
OPENAI_BASE_URL=https://your-org--agent-container-serve-sglang-serve.modal.run \
  make example BACKEND=opencode
```

If `make example BACKEND=opencode` produces a non-empty diff and opens a PR, Phase 3 is done
and SGLang becomes a supported production profile. If tool calling still crashes, the failure
scopes entirely to the SGLang inference layer — the opencode proxy is proven clean by Phase 2.

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
