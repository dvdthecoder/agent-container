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

Three profiles are built into `modal/serve.py`, selected by `SERVE_PROFILE`:

| Profile | Engine | Model | GPU | Context | Deploy command |
|---------|--------|-------|-----|---------|----------------|
| `test` (default) | vLLM | Qwen2.5-Coder-32B | A100 80GB | 32k | `modal deploy modal/serve.py` |
| `prod` | vLLM | Qwen3-Coder-80B (default) | 2× A100 80GB | 128k | `SERVE_PROFILE=prod modal deploy modal/serve.py` |
| `prod` + `SERVE_MODEL=minimax-m2.5` | vLLM | MiniMax-M2.5 | 8× A100 80GB | 1M | `SERVE_PROFILE=prod SERVE_MODEL=minimax-m2.5 modal deploy modal/serve.py` |
| `experiment` | SGLang | Qwen2.5-Coder-32B | A10G | 32k | `SERVE_PROFILE=experiment modal deploy modal/serve.py` |

**Start with `test`** — cheap, fast iteration. Promote to `prod` for production-grade output
quality. Use `SERVE_MODEL=minimax-m2.5` inside `prod` for maximum quality (SWE-bench grade).
Use `experiment` to run SGLang instead of vLLM — see the comparison table below.

`prod` model selection is driven by `SERVE_MODEL`. Add new models to the `_PROD_MODELS` dict
in `modal/serve.py` without touching the profile structure.

`experiment` deploys to a **separate** Modal app (`agent-container-serve-experiment`) so the
vLLM endpoint is never disturbed — both can run simultaneously.

### Model names

Set `OPENCODE_MODEL` to match `SERVED_MODEL_NAME` in `modal/serve.py`:

| Profile | `OPENCODE_MODEL` |
|---------|-----------------|
| `test` | `qwen2.5-coder` |
| `prod` (default model) | `qwen3-coder` |
| `prod` + `SERVE_MODEL=minimax-m2.5` | `minimax-m2.5` |
| `experiment` | `qwen2.5-coder` |

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
| Status | Default — `test` and `prod` profiles | Validated alternative (`experiment` profile) |
| Tool calling | Stable — `hermes` parser, works out of the box | Works with `hermes` parser; `qwen`/`qwen25` hangs |
| Base image | `debian_slim` — no CUDA toolkit needed | `nvidia/cuda:12.4.1-devel-ubuntu22.04` + `libnuma1` required |
| JIT compilation | None at startup | Compiles rope/attention kernels via TVM at model-load time |
| CUDA graphs | Enabled by default | Must disable (`--disable-cuda-graph`) on Modal |
| KV cache sharing | Manual prefix caching | RadixAttention — automatic, fine-grained sharing across runs |
| Throughput (shared prefix) | Baseline | 2–4× higher when runs share system prompt / repo context |
| Constrained decoding | Basic | Native JSON schema and regex enforcement at decode level |
| Modal app | `agent-container-serve` | `agent-container-serve-experiment` (separate, runs simultaneously) |
| Cold start (32B, A100) | ~1–2 min | ~2–3 min (JIT compile adds ~1 min) |
| Recommended for | All use cases today | Team-scale concurrent runs; benchmarking |

**Why vLLM is the default:** It works out-of-the-box with a standard Python base image and
has reliable tool calling across all model profiles. SGLang v0.4.7 (the original inference
server) had blocking bugs — `--tool-call-parser qwen25` crashed on the first tool-schema
request and streaming with tools hung indefinitely. Phase 1 switched to vLLM and removed all
SGLang-specific workarounds from the proxy (389 lines removed in Phase 2).

**When SGLang becomes worth it:** SGLang's primary advantage is **RadixAttention** — it builds
a radix tree of KV cache blocks and automatically reuses them across requests that share a
common prefix. For agent workloads this matters at scale: if 10 runs all start with the same
system prompt and the same repo context, SGLang serves runs 2–10 with the shared prefix already
cached. At low volume (one run at a time) vLLM and SGLang perform similarly. At team scale
(5+ engineers, concurrent runs against the same repo) SGLang's throughput advantage compounds.

| Advantage | Matters now (low volume) | Matters at team scale |
|-----------|--------------------------|----------------------|
| RadixAttention (KV cache sharing) | Minimal — runs are sequential | Yes — concurrent runs share repo context |
| Higher throughput | No | Yes — 2–4× on shared-prefix workloads |
| Constrained decoding | Not needed | Useful if tool call reliability degrades |
| Piecewise CUDA graphs | Disabled on Modal | Yes — if CUDA toolkit available in image |

### SGLang — Phase 3 validation results

Phase 3 re-tested SGLang in isolation against the same model (Qwen2.5-Coder 32B, A10G) to
determine whether newer versions had fixed the tool-calling bugs. The `experiment` profile deploys
to a **separate Modal app** (`agent-container-serve-experiment`) so the vLLM endpoint is never
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

**To run the experiment profile:**

```bash
SERVE_PROFILE=experiment modal deploy modal/serve.py
# → https://your-org--agent-container-serve-experiment-serve.modal.run

OPENAI_BASE_URL=https://your-org--agent-container-serve-experiment-serve.modal.run \
  make example BACKEND=opencode
```

---

## Scale-to-zero and cold starts

Modal scales the model server to zero after `scaledown_window` seconds of inactivity.
You pay only for active inference time.

| Profile | Cold start |
|---------|-----------|
| `test` | ~1–2 min |
| `prod` (qwen3-coder) | ~3–5 min |
| `prod` + `SERVE_MODEL=minimax-m2.5` | ~8–12 min |
| `experiment` | ~2–3 min (JIT compile adds ~1 min) |

Model weights are stored in a Modal Volume (`agent-container-models`) and are not
re-downloaded on cold start after the first deploy.

The agent sandbox waits for the model to be ready before starting (preflight probe with
configurable timeout via `SERVE_COLDSTART_BUDGET`).
