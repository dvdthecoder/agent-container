# Model Setup

The agent container is model-agnostic. It injects three environment variables into the sandbox
container and the coding agent picks them up:

```bash
OPENAI_BASE_URL=...    # any OpenAI-compatible endpoint
OPENAI_API_KEY=...     # API key or placeholder ("local", "modal", etc.)
OPENCODE_MODEL=...     # model identifier
```

Pick the option that fits your team.

---

## Option A — Together.ai / Fireworks (recommended to start)

No GPU, no infrastructure. Pay per token. Both providers run open models (Qwen3-Coder, DeepSeek-Coder)
on their own GPU clusters. Your prompts go to their servers for inference only — not training, not
fine-tuning.

=== "Together.ai"
    ```bash
    OPENAI_BASE_URL=https://api.together.xyz/v1
    OPENAI_API_KEY=your-together-key
    OPENCODE_MODEL=Qwen/Qwen3-Coder-80B-Instruct
    ```

=== "Fireworks.ai"
    ```bash
    OPENAI_BASE_URL=https://api.fireworks.ai/inference/v1
    OPENAI_API_KEY=your-fireworks-key
    OPENCODE_MODEL=accounts/fireworks/models/qwen3-coder-80b-instruct
    ```

**Cost**: ~$0.05–$0.40 per agent run depending on task length.

---

## Option B — Modal GPU deployment (self-hosted, no own hardware)

Deploy an open model on Modal's GPU infrastructure. You control the deployment, the model weights
never leave Modal's isolated container, and billing is per GPU second with scale-to-zero.

```bash
# deploy once
modal deploy modal/serve.py

# output: https://your-org--qwen-coder.modal.run/v1
```

Then:
```bash
OPENAI_BASE_URL=https://your-org--qwen-coder.modal.run/v1
OPENAI_API_KEY=modal
OPENCODE_MODEL=Qwen/Qwen3-Coder-80B
```

**Cold start**: ~45–60s for an 80B model on A100. Subsequent requests are fast (model stays warm
for a configurable idle period). Scale-to-zero when not in use.

**Cost**: ~$2.80/hr for an A100 80GB on Modal. At ~3 minutes per run, that's ~$0.14/run if the
container is warm. Cheaper if you run batches.

---

## Option C — Self-hosted SGLang (air-gap, enterprise on-prem)

For regulated environments where prompts cannot leave your network at all. Run SGLang on your own
GPU server.

**Hardware requirement**: A100 80GB or 2× RTX 4090 minimum for Qwen3-Coder 80B.

```bash
# on your GPU server
pip install sglang
python -m sglang.launch_server \
  --model Qwen/Qwen3-Coder-80B \
  --port 30000 \
  --tensor-parallel-size 2

# in .env
OPENAI_BASE_URL=http://your-gpu-server:30000/v1
OPENAI_API_KEY=local
OPENCODE_MODEL=Qwen/Qwen3-Coder-80B
```

**Why SGLang over vLLM for agent workloads**: SGLang's RadixAttention maintains a shared KV cache
tree across all requests. The OpenCode system prompt (identical for every run) is computed once and
cached. At team scale (hundreds of runs/day), this eliminates 40–70% of total compute vs. vLLM.

| Server | Relative throughput | Prefix cache | Concurrent requests |
|---|---|---|---|
| Ollama | 1× | Basic | Queued |
| vLLM | 3× | Simple match | Batched |
| SGLang | 6× | Radix tree (shared) | Batched + shared |

---

## Option D — Anthropic / Gemini API

```bash
# Claude backend
ANTHROPIC_API_KEY=sk-ant-...
OPENCODE_MODEL=claude-sonnet-4-6

# Gemini backend
GEMINI_API_KEY=...
OPENCODE_MODEL=gemini-2.5-pro
```

!!! note
    When using the `claude` or `gemini` agent backend (not OpenCode), the relevant API key is
    read directly by the CLI inside the container — `OPENAI_BASE_URL` is not used.

---

## Model recommendations

| Use case | Recommended model | Why |
|---|---|---|
| Best coding quality | Qwen3-Coder 80B | 70.6 SWE-bench, purpose-built for code |
| Faster / cheaper | Qwen2.5-Coder 14B | Good quality, 5× cheaper to run |
| No open model | Claude Sonnet 4.6 | Best proprietary coding model |
| Budget CI runs | Claude Haiku 4.5 | ~$0.05/run, sufficient for e2e tests |

The [Onyx self-hosted LLM leaderboard](https://onyx.app/self-hosted-llm-leaderboard) is a useful
reference for tracking how open models compare on real coding tasks.
