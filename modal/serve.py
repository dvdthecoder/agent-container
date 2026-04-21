"""Deploy Qwen3-Coder on Modal GPU — SGLang OpenAI-compatible inference endpoint.

SGLang's RadixAttention automatically caches shared KV prefixes across requests.
Agent runs that share a long system prompt + repo context (the common case here)
get prefix cache hits on every run after the first, which improves throughput
significantly vs a stateless inference server.

Usage
-----
# Test profile (8B, single A10G — cheap, fast cold start)
modal deploy modal/serve.py

# Production profile (80B, 2× A100 80GB)
SERVE_PROFILE=prod modal deploy modal/serve.py

After deployment Modal prints the endpoint URL:
  ✓ Created web endpoint: https://your-org--agent-container-serve.modal.run

Add it to .env:
  OPENAI_BASE_URL=https://your-org--agent-container-serve.modal.run/v1
  OPENAI_API_KEY=modal
  OPENCODE_MODEL=qwen3-coder
"""

from __future__ import annotations

import os
import subprocess

import modal

# ── Profile configuration ────────────────────────────────────────────────────

SERVE_PROFILE = os.environ.get("SERVE_PROFILE", "test")

if SERVE_PROFILE == "prod":
    MODEL_ID = "Qwen/Qwen3-Coder-80B-Instruct"
    GPU: str | modal.gpu.A100 = modal.gpu.A100(count=2, size="80GB")
    CONTEXT_LENGTH = 131_072
    TP_SIZE = 2                # tensor parallelism across both A100s
    SCALEDOWN_WINDOW = 600     # stay warm 10 min (cold start is expensive at 80B)
    STARTUP_TIMEOUT = 360      # 80B model takes longer to load
else:
    # test — Qwen3-Coder 8B on a single A10G (~$1/hr, ~30s cold start)
    MODEL_ID = "Qwen/Qwen3-Coder-8B-Instruct"
    GPU = "A10G"
    CONTEXT_LENGTH = 32_768
    TP_SIZE = 1
    SCALEDOWN_WINDOW = 300     # stay warm 5 min
    STARTUP_TIMEOUT = 180

# ── Modal app ────────────────────────────────────────────────────────────────

app = modal.App("agent-container-serve")

# Persistent volume — model weights cached here, not re-downloaded on cold start
model_volume = modal.Volume.from_name("agent-container-models", create_if_missing=True)

# Use the official SGLang Docker image as the base — it ships with all CUDA
# libraries and FlashInfer attention kernels pre-built for CUDA 12.4.
# We add huggingface_hub on top for faster weight downloads via hf_transfer.
image = (
    modal.Image.from_registry("lmsysorg/sglang:v0.5.8-cu124")
    .pip_install("huggingface_hub[hf_transfer]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})  # faster weight downloads
)

# ── Serve function ───────────────────────────────────────────────────────────


@app.function(
    image=image,
    gpu=GPU,
    secrets=[modal.Secret.from_name("huggingface")],
    timeout=60 * 60,  # 1 hour per request max
    scaledown_window=SCALEDOWN_WINDOW,
    volumes={"/model-cache": model_volume},
    allow_concurrent_inputs=32,
)
@modal.web_server(port=8000, startup_timeout=STARTUP_TIMEOUT)
def serve() -> None:
    """Start SGLang OpenAI-compatible server inside the Modal container."""
    cmd = [
        "python", "-m", "sglang.launch_server",
        "--model", MODEL_ID,
        "--download-dir", "/model-cache",
        "--served-model-name", "qwen3-coder",
        "--host", "0.0.0.0",  # noqa: S104 — intentional, container-internal binding
        "--port", "8000",
        "--context-length", str(CONTEXT_LENGTH),
        "--mem-fraction-static", "0.88",  # leave headroom for RadixAttention KV cache
        "--trust-remote-code",
    ]
    if TP_SIZE > 1:
        cmd += ["--tp", str(TP_SIZE)]
    subprocess.Popen(cmd)
