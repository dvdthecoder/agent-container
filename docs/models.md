# Model Setup

The model runs on Modal GPU infrastructure, deployed once and called by the sandbox over Modal's
internal network. The inference server is [SGLang](https://github.com/sgl-project/sglang) —
an open-source, high-performance serving framework built for agent and reasoning workloads.

## Deploy the model

```bash
modal deploy modal/serve.py
```

This starts Qwen3-Coder on an A100 GPU and exposes an internal Modal endpoint. The sandbox
container calls it automatically — no URL configuration needed, it's wired up inside Modal's
network.

Scale-to-zero when idle. You pay only for GPU seconds while the model is actually processing a
request.

## Profiles

`modal/serve.py` ships with two profiles:

| Profile | Model | GPU | Context | Use case |
|---|---|---|---|---|
| `test` (default) | Qwen3-Coder 8B | A10G | 32 k | Development, CI, low-cost runs |
| `prod` | Qwen3-Coder 80B | 2× A100 80GB | 128 k | Production, large repos |

```bash
# test profile (default)
modal deploy modal/serve.py

# production profile
SERVE_PROFILE=prod modal deploy modal/serve.py
```

## Why SGLang

SGLang's **RadixAttention** automatically caches shared KV prefixes across requests using a radix
tree. Agent runs against the same repo repeatedly re-use the cached representation of the system
prompt and repo context — only the task-specific tokens need to be computed from scratch.

This matters for agent-container because:

- Every run against the same repo shares a large common prefix (system prompt + file tree)
- Multiple CI runs against the same repo in parallel hit the cache simultaneously
- Per-token latency stays stable as concurrency grows

Benchmarks show ~29% higher throughput vs vLLM on prefix-heavy workloads (H100, ShareGPT-style).
For purely unique-prompt workloads the gap closes, but the agent pattern is prefix-heavy by nature.

SGLang also exposes the same OpenAI-compatible API as vLLM — no changes to the sandbox, CLI, or
any calling code are required when switching between inference backends.

## Why Qwen3-Coder

Qwen3-Coder 80B scores **70.6 on SWE-bench** — the standard benchmark for real code editing on
real repositories. It is purpose-built for software engineering tasks: reading large codebases,
understanding context, making targeted edits, writing tests.

The 80B model fits on 2× A100 80GB with tensor parallelism (`--tp 2`), which is what the `prod`
profile provisions. The 8B test model fits on a single A10G.

## Why Modal for the model

- No GPU hardware to buy or manage
- Same infrastructure as the sandbox — one platform, one bill, one set of credentials
- Internal network between sandbox and model — no public internet hop, lower latency
- Scale-to-zero: costs nothing when no agent runs are happening

## Agent backends and model coupling

| Backend | Model |
|---|---|
| `opencode` | Qwen3-Coder via Modal SGLang endpoint (default) |
| `claude` | Anthropic API (Claude Code CLI reads `ANTHROPIC_API_KEY`) |
| `gemini` | Google AI / Vertex (Gemini CLI reads `GEMINI_API_KEY`) |

The `opencode` backend is the default and recommended path — it uses the Modal-hosted model,
keeping everything within Modal's infrastructure.

The `claude` and `gemini` backends are available for teams already standardised on those CLIs.
When using them, prompts go to Anthropic or Google respectively.
