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
production-grade output quality. Use `sglang` to run on SGLang instead of vLLM — see the
comparison table below.

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

### vLLM vs SGLang

| | vLLM | SGLang |
|--|------|--------|
| Status | Default — all production profiles | Validated alternative (`sglang` profile) |
| Tool calling | Stable — `hermes` parser, works out of the box | Works with `hermes` parser; `qwen`/`qwen25` hangs |
| Base image | `debian_slim` — no CUDA toolkit needed | `nvidia/cuda:12.4.1-devel-ubuntu22.04` + `libnuma1` required |
| JIT compilation | None at startup | Compiles rope/attention kernels via TVM at model-load time |
| CUDA graphs | Enabled by default | Must disable (`--disable-cuda-graph`) on Modal |
| Modal app | `agent-container-serve` | `agent-container-serve-sglang` (separate, runs simultaneously) |
| Cold start (7B, A10G) | ~1–2 min | ~2–3 min (JIT compile adds ~1 min) |
| Recommended for | All use cases | Benchmarking, validation, or if you prefer SGLang's runtime |

**Why vLLM is the default:** It works out-of-the-box with a standard Python base image and
has reliable tool calling across all model profiles. SGLang v0.4.7 (the original inference
server) had blocking bugs — `--tool-call-parser qwen25` crashed on the first tool-schema
request and streaming with tools hung indefinitely. Phase 1 switched to vLLM and removed all
SGLang-specific workarounds from the proxy (389 lines removed in Phase 2).

### SGLang — Phase 3 validation results

Phase 3 re-tested SGLang in isolation against the same model (Qwen2.5-Coder 7B, A10G) to
determine whether newer versions had fixed the tool-calling bugs. The `sglang` profile deploys
to a **separate Modal app** (`agent-container-serve-sglang`) so the vLLM endpoint is never
disturbed — both can run simultaneously.

**Results (Phase 3 complete):**

| Parser | Result |
|--------|--------|
| `qwen` / `qwen25` | Hangs — 0 chunks received on first tool-schema request |
| `hermes` | Works — 10 tools, responds in 3 seconds, full run in 29 seconds |

SGLang also requires extra image setup that vLLM does not:
- Base image must be `nvidia/cuda:12.4.1-devel-ubuntu22.04` (not `debian_slim`) — SGLang
  JIT-compiles rope/attention kernels at model-load time via its own TVM layer, requiring
  `nvcc` and CUDA headers
- `libnuma1` must be installed — the SM86 (A10G) `sgl_kernel` binary links against
  `libnuma.so.1`
- CUDA graph capture must be disabled (`--disable-cuda-graph`) on first boot

**To run the SGLang profile:**

```bash
SERVE_PROFILE=sglang modal deploy modal/serve.py
# → https://your-org--agent-container-serve-sglang-serve.modal.run

OPENAI_BASE_URL=https://your-org--agent-container-serve-sglang-serve.modal.run \
  make example BACKEND=opencode
```

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
