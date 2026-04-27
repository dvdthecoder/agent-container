"""Deploy a coding model on Modal GPU — SGLang OpenAI-compatible inference endpoint.

SGLang's RadixAttention automatically caches shared KV prefixes across requests.
Agent runs that share a long system prompt + repo context (the common case here)
get prefix cache hits on every run after the first.

Profiles
--------
test    — Qwen3-Coder 8B   on A10G        (cheap, ~30s cold start)
prod    — Qwen3-Coder 80B  on 2× A100 80G (128k context, tensor-parallel)
minimax — MiniMax M2.5     on 8× A100 80G (1M context, MoE, top SWE-bench)

Usage
-----
modal deploy modal/serve.py                        # test (Qwen3-Coder 8B)
SERVE_PROFILE=prod modal deploy modal/serve.py     # prod (Qwen3-Coder 80B)
SERVE_PROFILE=minimax modal deploy modal/serve.py  # MiniMax M2.5

After deployment Modal prints the endpoint URL:
  ✓ Created web endpoint: https://your-org--agent-container-serve.modal.run

Add it to .env:
  OPENAI_BASE_URL=https://your-org--agent-container-serve.modal.run/v1
  OPENAI_API_KEY=modal
  OPENCODE_MODEL=<SERVED_MODEL_NAME from profile below>

Alternatively, use MiniMax's hosted API instead (no GPU required):
  OPENAI_BASE_URL=https://api.minimax.io/v1
  OPENAI_API_KEY=<your-minimax-api-key>
  OPENCODE_MODEL=MiniMax-M2.5
"""

from __future__ import annotations

import os
import subprocess

import modal

# ── Profile configuration ────────────────────────────────────────────────────

SERVE_PROFILE = os.environ.get("SERVE_PROFILE", "test")

if SERVE_PROFILE == "minimax":
    # MiniMax M2.5 — #1 on SWE-bench as of 2026-04.
    # MoE architecture: 456B total / ~45B active params per token.
    # Lightning Attention supports up to 1M context natively.
    # Requires 8× A100 80GB for full-precision; SGLang supports it via --tp 8.
    # HuggingFace: https://huggingface.co/MiniMaxAI/MiniMax-M2.5
    MODEL_ID = "MiniMaxAI/MiniMax-M2.5"
    SERVED_MODEL_NAME = "minimax-m2.5"
    GPU: str | modal.gpu.A100 = modal.gpu.A100(count=8, size="80GB")
    CONTEXT_LENGTH = 1_000_000  # 1M context — use what you need, cap for cost
    TP_SIZE = 8
    SCALEDOWN_WINDOW = 600  # stay warm 10 min — cold start is expensive at 8×
    STARTUP_TIMEOUT = 600  # large model + 8 GPUs need longer to initialise
    MEM_FRACTION = 0.90  # MoE keeps fewer weights resident per device

elif SERVE_PROFILE == "prod":
    MODEL_ID = "Qwen/Qwen3-Coder-80B-Instruct"
    SERVED_MODEL_NAME = "qwen3-coder"
    GPU = modal.gpu.A100(count=2, size="80GB")
    CONTEXT_LENGTH = 131_072
    TP_SIZE = 2
    SCALEDOWN_WINDOW = 600
    STARTUP_TIMEOUT = 360
    MEM_FRACTION = 0.88

else:
    # test — Qwen3-Coder 8B on a single A10G (~$1/hr, ~30s cold start)
    MODEL_ID = "Qwen/Qwen3-Coder-8B-Instruct"
    SERVED_MODEL_NAME = "qwen3-coder"
    GPU = "A10G"
    CONTEXT_LENGTH = 32_768
    TP_SIZE = 1
    SCALEDOWN_WINDOW = 300
    STARTUP_TIMEOUT = 180
    MEM_FRACTION = 0.88

# ── Modal app ────────────────────────────────────────────────────────────────

app = modal.App("agent-container-serve")

# Persistent volume — model weights cached here, not re-downloaded on cold start
model_volume = modal.Volume.from_name("agent-container-models", create_if_missing=True)

# SGLang Docker image ships with all CUDA libraries and FlashInfer kernels.
# huggingface_hub[hf_transfer] accelerates weight downloads.
image = (
    modal.Image.from_registry("lmsysorg/sglang:v0.5.8-cu124")
    .pip_install("huggingface_hub[hf_transfer]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)

# ── Serve function ───────────────────────────────────────────────────────────


@app.function(
    image=image,
    gpu=GPU,
    secrets=[modal.Secret.from_name("huggingface")],
    timeout=60 * 60,
    scaledown_window=SCALEDOWN_WINDOW,
    volumes={"/model-cache": model_volume},
    allow_concurrent_inputs=32,
)
@modal.web_server(port=8000, startup_timeout=STARTUP_TIMEOUT)
def serve() -> None:
    """Start SGLang OpenAI-compatible server inside the Modal container."""
    cmd = [
        "python",
        "-m",
        "sglang.launch_server",
        "--model",
        MODEL_ID,
        "--download-dir",
        "/model-cache",
        "--served-model-name",
        SERVED_MODEL_NAME,
        "--host",
        "0.0.0.0",  # noqa: S104 — container-internal binding
        "--port",
        "8000",
        "--context-length",
        str(CONTEXT_LENGTH),
        "--mem-fraction-static",
        str(MEM_FRACTION),
        "--trust-remote-code",
    ]
    if TP_SIZE > 1:
        cmd += ["--tp", str(TP_SIZE)]
    subprocess.Popen(cmd)  # noqa: S603 — cmd is fully hardcoded; TP_SIZE is an int
