"""Deploy a coding model on Modal GPU — vLLM OpenAI-compatible inference endpoint.

Profiles
--------
test    — Qwen2.5-Coder 7B   on A10G        (cheap, ~2 min cold start)
prod    — Qwen3-Coder 80B    on 2× A100 80G (128k context, tensor-parallel)
minimax — MiniMax M2.5       on 8× A100 80G (1M context, MoE, top SWE-bench)

Usage
-----
modal deploy modal/serve.py                        # test (Qwen2.5-Coder 7B)
SERVE_PROFILE=prod modal deploy modal/serve.py     # prod (Qwen3-Coder 80B)
SERVE_PROFILE=minimax modal deploy modal/serve.py  # MiniMax M2.5

After deployment Modal prints the endpoint URL:
  ✓ Created web function serve => https://your-org--agent-container-serve-serve.modal.run

Add it to .env (no /v1 suffix):
  OPENAI_BASE_URL=https://your-org--agent-container-serve-serve.modal.run
  OPENAI_API_KEY=modal
  OPENCODE_MODEL=<SERVED_MODEL_NAME from profile below>
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import modal

# Load .env so HF_TOKEN is available when building the Modal secret locally.
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

# ── Profile configuration ────────────────────────────────────────────────────

SERVE_PROFILE = os.environ.get("SERVE_PROFILE", "test")

if SERVE_PROFILE == "minimax":
    # MiniMax M2.5 — #1 on SWE-bench as of 2026-04.
    # MoE architecture: 456B total / ~45B active params per token.
    # Lightning Attention supports up to 1M context natively.
    MODEL_ID = "MiniMaxAI/MiniMax-M2.5"
    SERVED_MODEL_NAME = "minimax-m2.5"
    GPU: str | modal.gpu.A100 = modal.gpu.A100(count=8, size="80GB")
    CONTEXT_LENGTH = 1_000_000
    TP_SIZE = 8
    SCALEDOWN_WINDOW = 600
    STARTUP_TIMEOUT = 600
    TOOL_CALL_PARSER = "hermes"

elif SERVE_PROFILE == "prod":
    MODEL_ID = "Qwen/Qwen3-Coder-80B-Instruct"
    SERVED_MODEL_NAME = "qwen3-coder"
    GPU = modal.gpu.A100(count=2, size="80GB")
    CONTEXT_LENGTH = 131_072
    TP_SIZE = 2
    SCALEDOWN_WINDOW = 600
    STARTUP_TIMEOUT = 360
    TOOL_CALL_PARSER = "hermes"

else:
    # test — Qwen2.5-Coder 7B on A10G (~$1/hr, ~2 min cold start)
    MODEL_ID = "Qwen/Qwen2.5-Coder-7B-Instruct"
    SERVED_MODEL_NAME = "qwen2.5-coder"
    GPU = "A10G"
    CONTEXT_LENGTH = 32_768
    TP_SIZE = 1
    SCALEDOWN_WINDOW = 300
    STARTUP_TIMEOUT = 300
    TOOL_CALL_PARSER = "hermes"

# ── Modal app ────────────────────────────────────────────────────────────────

app = modal.App("agent-container-serve")

# Persistent volume — model weights cached here, not re-downloaded on cold start
model_volume = modal.Volume.from_name("agent-container-models", create_if_missing=True)

# vLLM image — ships with CUDA libraries and the OpenAI-compatible API server.
# huggingface_hub[hf_transfer] accelerates weight downloads.
image = (
    modal.Image.from_registry("vllm/vllm-openai:latest", add_python="3.11")
    .pip_install("huggingface_hub[hf_transfer]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)

# ── Serve function ───────────────────────────────────────────────────────────


@app.function(
    image=image,
    gpu=GPU,
    secrets=[modal.Secret.from_dict({"HF_TOKEN": os.environ["HF_TOKEN"]})],
    timeout=60 * 60,
    scaledown_window=SCALEDOWN_WINDOW,
    volumes={"/model-cache": model_volume},
)
@modal.concurrent(max_inputs=32)
@modal.web_server(port=8000, startup_timeout=STARTUP_TIMEOUT)
def serve() -> None:
    """Start vLLM OpenAI-compatible server inside the Modal container."""
    cmd = [
        "python3", "-m", "vllm.entrypoints.openai.api_server",
        "--model", MODEL_ID,
        "--download-dir", "/model-cache",
        "--served-model-name", SERVED_MODEL_NAME,
        "--host", "0.0.0.0",  # noqa: S104 — container-internal binding
        "--port", "8000",
        "--max-model-len", str(CONTEXT_LENGTH),
        "--trust-remote-code",
    ]
    if TP_SIZE > 1:
        cmd += ["--tensor-parallel-size", str(TP_SIZE)]
    # Tool calling — required for opencode backend (Phase 2).
    # Not needed for aider backend (Phase 1) which uses diff format.
    if TOOL_CALL_PARSER:
        cmd += ["--enable-auto-tool-choice", "--tool-call-parser", TOOL_CALL_PARSER]
    subprocess.Popen(cmd)  # noqa: S603 — cmd is fully hardcoded; TP_SIZE is an int
