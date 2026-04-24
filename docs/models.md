# Model Setup

The sandbox passes three env vars into every container run:

```bash
OPENAI_BASE_URL=...   # any OpenAI-compatible endpoint
OPENAI_API_KEY=...    # key for that endpoint
OPENCODE_MODEL=...    # model name the endpoint accepts
```

Any OpenAI-compatible inference server works — hosted or self-hosted.

---

## Recommended: DeepSeek V4 Pro

DeepSeek V4 Pro is the best model for agent-container workloads today — top-tier coding benchmark
scores, 1M context window (handles large repos without truncation), and low cost per run.

| Property | deepseek-v4-pro | deepseek-v4-flash |
|---|---|---|
| Context window | 1 000 000 tokens | 1 000 000 tokens |
| Output tokens max | 384 000 | — |
| Input (cache miss) | $1.74 / M tokens | $0.14 / M tokens |
| Output | $3.48 / M tokens | $0.28 / M tokens |
| Aider polyglot score | ~74% (V3.2 series) | — |
| Best for | Full agent runs, large repos | High-volume, cost-sensitive |

### Setup

Get an API key at **platform.deepseek.com** → API Keys:

```bash
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_API_KEY=your-deepseek-api-key
OPENCODE_MODEL=deepseek-v4-pro        # or deepseek-v4-flash for ~10× cheaper
```

That's it. No deployment step required.

---

## Alternative: MiniMax M2.5 / M2.7

MiniMax scored well on SWE-bench and offers extremely low per-instance cost. Use it if you want
to run high volumes of shorter tasks.

| Property | Value |
|---|---|
| Context window | 1 000 000 tokens |
| Cost (SWE-bench instance) | $0.073 |
| Architecture | MoE + Lightning Attention |
| Latest model | MiniMax-M2.7 |

```bash
OPENAI_BASE_URL=https://api.minimax.io/v1
OPENAI_API_KEY=your-minimax-api-key   # platform.minimax.io → API Keys
OPENCODE_MODEL=MiniMax-M2.5           # or MiniMax-M2.7 (latest), MiniMax-M2.5-highspeed
```

---

## Self-hosted on Modal GPU

For full air-gap or maximum control, deploy the model on Modal. Three profiles:

| Profile | Model | GPU | Context | Command |
|---|---|---|---|---|
| `test` | Qwen3-Coder 8B | A10G | 32 k | `modal deploy modal/serve.py` |
| `prod` | Qwen3-Coder 80B | 2× A100 80GB | 128 k | `SERVE_PROFILE=prod modal deploy modal/serve.py` |
| `minimax` | MiniMax M2.5 | 8× A100 80GB | 1 M | `SERVE_PROFILE=minimax modal deploy modal/serve.py` |

After deployment Modal prints the endpoint URL:

```bash
OPENAI_BASE_URL=https://your-org--agent-container-serve.modal.run/v1
OPENAI_API_KEY=modal
OPENCODE_MODEL=deepseek-v4-pro    # or minimax-m2.5 / qwen3-coder
```

---

## Model comparison

| Model | Aider score | Cost/run (est.) | Context | Hosted API |
|---|---|---|---|---|
| GPT-5 | 88% | $17–29 | — | OpenAI |
| Gemini 2.5 Pro | 83% | $45–50 | 1M | Google |
| o3 | 77% | $13.75 | — | OpenAI |
| **DeepSeek V4 Pro** | **~74%** | **~$1–3** | **1M** | **DeepSeek** |
| DeepSeek V4 Flash | — | ~$0.10–0.30 | 1M | DeepSeek |
| MiniMax M2.5 | (SWE-bench #1) | ~$0.07/instance | 1M | MiniMax |

DeepSeek V4 Pro hits the quality/cost sweet spot for automated agent runs in CI — good enough
to ship production PRs, cheap enough to run on every PR or nightly.

---

## Why SGLang (for self-hosted)

SGLang's **RadixAttention** automatically caches shared KV prefixes across requests. Agent runs
against the same repo repeatedly re-use the cached representation of the system prompt and repo
context — only the task-specific tokens are computed from scratch.

This matters for agent-container because:

- Every run against the same repo shares a large common prefix (system prompt + file tree)
- Multiple CI runs against the same repo in parallel hit the cache simultaneously

---

## Agent backends and model coupling

| Backend | Model source |
|---|---|
| `opencode` | Any endpoint via `OPENAI_BASE_URL` — DeepSeek, MiniMax, or self-hosted |
| `claude` | Anthropic API (`ANTHROPIC_API_KEY`) |
| `gemini` | Google AI / Vertex (`GEMINI_API_KEY`) |

The `opencode` backend is the default. Swap the three env vars to change models — no code changes.
