"""Deploy Qwen3-Coder on Modal GPU — OpenAI-compatible inference endpoint.

Usage
-----
# Test profile (8B, A10G — cheap, fast cold start)
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
    MAX_MODEL_LEN = 131_072
    SCALEDOWN_WINDOW = 600  # stay warm 10 min (cold start is expensive at 80B)
else:
    # test — Qwen3-Coder 8B on a single A10G (~$1/hr, ~30s cold start)
    MODEL_ID = "Qwen/Qwen3-Coder-8B-Instruct"
    GPU = "A10G"
    MAX_MODEL_LEN = 32_768
    SCALEDOWN_WINDOW = 300  # stay warm 5 min

# ── Modal app ────────────────────────────────────────────────────────────────

app = modal.App("agent-container-serve")

# Persistent volume — model weights cached here, not re-downloaded on cold start
model_volume = modal.Volume.from_name("agent-container-models", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "vllm>=0.4.0",
        "huggingface_hub[hf_transfer]",
    )
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
@modal.web_server(port=8000, startup_timeout=180)
def serve() -> None:
    """Start vLLM OpenAI-compatible server inside the Modal container."""
    subprocess.Popen(
        [
            "python",
            "-m",
            "vllm.entrypoints.openai.api_server",
            "--model",
            MODEL_ID,
            "--download-dir",
            "/model-cache",
            "--served-model-name",
            "qwen3-coder",
            "--host",
            "0.0.0.0",  # noqa: S104 — intentional, container-internal binding
            "--port",
            "8000",
            "--max-model-len",
            str(MAX_MODEL_LEN),
            "--trust-remote-code",
        ]
    )
