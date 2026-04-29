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
  ✓ Created web function serve => https://your-org--agent-container-serve-serve.modal.run

Add it to .env (no /v1 suffix — the OpenAI SDK adds that itself):
  OPENAI_BASE_URL=https://your-org--agent-container-serve-serve.modal.run
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
from pathlib import Path

import modal

# Load .env so HF_TOKEN is available when building the Modal secret locally.
# python-dotenv is a dev dependency — not installed in the SGLang container.
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
    TOOL_CALL_PARSER = "hermes"  # MiniMax uses Hermes tool format

elif SERVE_PROFILE == "prod":
    MODEL_ID = "Qwen/Qwen3-Coder-80B-Instruct"
    SERVED_MODEL_NAME = "qwen3-coder"
    GPU = modal.gpu.A100(count=2, size="80GB")
    CONTEXT_LENGTH = 131_072
    TP_SIZE = 2
    SCALEDOWN_WINDOW = 600
    STARTUP_TIMEOUT = 360
    MEM_FRACTION = 0.88
    TOOL_CALL_PARSER = "qwen25"  # Qwen3-Coder uses same tool format as Qwen2.5

else:
    # test — Qwen2.5-Coder 7B on a single A10G (~$1/hr, ~3 min cold start)
    MODEL_ID = "Qwen/Qwen2.5-Coder-7B-Instruct"
    SERVED_MODEL_NAME = "qwen2.5-coder"
    GPU = "A10G"
    CONTEXT_LENGTH = 32_768
    TP_SIZE = 1
    SCALEDOWN_WINDOW = 300
    STARTUP_TIMEOUT = 300  # 5 min — model load from volume takes ~3 min on A10G
    MEM_FRACTION = 0.88
    TOOL_CALL_PARSER = "qwen25"  # Qwen2.5-Coder native tool format

# ── Modal app ────────────────────────────────────────────────────────────────

app = modal.App("agent-container-serve")

# Persistent volume — model weights cached here, not re-downloaded on cold start
model_volume = modal.Volume.from_name("agent-container-models", create_if_missing=True)

# SGLang Docker image ships with all CUDA libraries and FlashInfer kernels.
# huggingface_hub[hf_transfer] accelerates weight downloads.
image = (
    # add_python="3.11" lets Modal detect the Python version for the function
    # runtime.  It installs a bare Python 3.11 but does NOT delete the image's
    # own Python (3.10) — we detect sglang's Python by version path at runtime.
    modal.Image.from_registry("lmsysorg/sglang:v0.4.7.post1-cu124", add_python="3.11")
    .run_commands("python3 -m pip install --break-system-packages 'huggingface_hub[hf_transfer]'")
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
    """Start SGLang OpenAI-compatible server inside the Modal container."""
    # add_python="3.11" puts a bare Python 3.11 first in PATH that has no sglang.
    # The image's own Python (typically 3.10) is still accessible at its
    # version-specific path.  Try candidates in order; fall back to python3.
    sglang_python = "python3"
    for candidate in [
        "python3.12",
        "python3.11",
        "python3.10",
        "python3.9",
        "/usr/bin/python3",
        "python3",
    ]:
        try:
            r = subprocess.run(  # noqa: S603
                [candidate, "-c", "import sglang"],
                capture_output=True,
                check=False,
            )
            if r.returncode == 0:
                sglang_python = candidate
                break
        except FileNotFoundError:
            continue
    print(f"[serve] using {sglang_python} for sglang.launch_server")  # noqa: T201

    cmd = [
        sglang_python,
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
    if TOOL_CALL_PARSER:
        cmd += ["--tool-call-parser", TOOL_CALL_PARSER, "--enable-auto-tool-choice"]
    subprocess.Popen(cmd)  # noqa: S603 — cmd is fully hardcoded; TP_SIZE is an int
