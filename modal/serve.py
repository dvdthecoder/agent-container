"""Deploy a coding model on Modal GPU — OpenAI-compatible inference endpoint.

Profiles
--------
test        — fixed config, zero decisions needed.
              Qwen2.5-Coder 32B · A100 80GB · vLLM  (~$4/hr, ~3 min cold start)
              Reliable tool use; use this profile for opencode runs.

prod        — flexible model, engine always vLLM.
              Default: Qwen3-Coder 80B · 2× A100 80GB
              Override: SERVE_MODEL=minimax-m2.5  →  MiniMax M2.5 · 8× A100 80GB

experiment  — SGLang engine, validated default model.
              Qwen2.5-Coder 7B · A10G · SGLang
              Deploys to agent-container-serve-experiment (separate app, non-destructive)

Usage
-----
modal deploy modal/serve.py                                   # test
SERVE_PROFILE=prod    modal deploy modal/serve.py             # prod (Qwen3-Coder 80B)
SERVE_PROFILE=prod SERVE_MODEL=minimax-m2.5 modal deploy ...  # prod (MiniMax M2.5)
SERVE_PROFILE=experiment modal deploy modal/serve.py          # experiment (SGLang)

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

# ── Profile + model configuration ────────────────────────────────────────────

SERVE_PROFILE = os.environ.get("SERVE_PROFILE", "test")

# ── prod model registry ───────────────────────────────────────────────────────
# SERVE_MODEL selects the model within the prod profile.
# Add new models here — GPU and context are inferred automatically.

_PROD_MODELS: dict[str, dict] = {
    "qwen3-coder": {
        "model_id": "Qwen/Qwen3-Coder-80B-Instruct",
        "served_name": "qwen3-coder",
        "gpu": modal.gpu.A100(count=2, size="80GB"),
        "context_length": 131_072,
        "tp_size": 2,
    },
    "minimax-m2.5": {
        # MiniMax M2.5 — MoE, 456B total / ~45B active, Lightning Attention (1M ctx)
        "model_id": "MiniMaxAI/MiniMax-M2.5",
        "served_name": "minimax-m2.5",
        "gpu": modal.gpu.A100(count=8, size="80GB"),
        "context_length": 1_000_000,
        "tp_size": 8,
    },
}
_PROD_DEFAULT = "qwen3-coder"

# ── Resolve profile variables ─────────────────────────────────────────────────

if SERVE_PROFILE == "prod":
    _model_key = os.environ.get("SERVE_MODEL", _PROD_DEFAULT)
    if _model_key not in _PROD_MODELS:
        raise ValueError(f"Unknown SERVE_MODEL={_model_key!r}. Available: {list(_PROD_MODELS)}")
    _m = _PROD_MODELS[_model_key]
    MODEL_ID: str = _m["model_id"]
    SERVED_MODEL_NAME: str = _m["served_name"]
    GPU: str | modal.gpu.A100 = _m["gpu"]
    CONTEXT_LENGTH: int = _m["context_length"]
    TP_SIZE: int = _m["tp_size"]
    SCALEDOWN_WINDOW = 600
    STARTUP_TIMEOUT = 600
    TOOL_CALL_PARSER = "hermes"

elif SERVE_PROFILE == "experiment":
    # SGLang engine — validated on Qwen2.5-Coder 7B + A10G with hermes parser.
    # Deploys to a separate Modal app so test/prod endpoints are never disturbed.
    # See docs/models.md for full SGLang setup notes (CUDA devel image, libnuma1).
    MODEL_ID = "Qwen/Qwen2.5-Coder-7B-Instruct"
    SERVED_MODEL_NAME = "qwen2.5-coder"
    GPU = "A10G"
    CONTEXT_LENGTH = 32_768
    TP_SIZE = 1
    SCALEDOWN_WINDOW = 300
    STARTUP_TIMEOUT = 600
    TOOL_CALL_PARSER = "hermes"

else:
    # test — zero config, fixed model + engine.
    # 32B chosen over 7B for reliable tool use with opencode; 7B frequently
    # responds with plain text instead of calling file-editing tools.
    MODEL_ID = "Qwen/Qwen2.5-Coder-32B-Instruct"
    SERVED_MODEL_NAME = "qwen2.5-coder-32b"
    GPU = modal.gpu.A100(count=1, size="80GB")
    CONTEXT_LENGTH = 32_768
    TP_SIZE = 1
    SCALEDOWN_WINDOW = 300
    STARTUP_TIMEOUT = 600
    TOOL_CALL_PARSER = "hermes"

# ── Modal app ────────────────────────────────────────────────────────────────

# experiment deploys to its own app so vLLM endpoints are never disturbed.
# test and prod share agent-container-serve.
_APP_NAME = (
    "agent-container-serve-experiment" if SERVE_PROFILE == "experiment" else "agent-container-serve"
)
app = modal.App(_APP_NAME)

# Persistent volume — model weights cached here across cold starts.
# All profiles share the same volume; weights downloaded once are reused.
model_volume = modal.Volume.from_name("agent-container-models", create_if_missing=True)

# ── Container image ───────────────────────────────────────────────────────────
# SGLang JIT-compiles CUDA kernels at model-load time via its own TVM layer.
# It requires the full CUDA toolkit (nvcc + headers) which debian_slim lacks.
# vLLM works with debian_slim — Modal injects GPU drivers at runtime.

if SERVE_PROFILE == "experiment":
    image = (
        modal.Image.from_registry(
            "nvidia/cuda:12.4.1-devel-ubuntu22.04",
            add_python="3.11",
        )
        # libnuma1 — runtime dep of sgl_kernel SM86 (A10G) binary.
        # Without it the .so fails to load and sgl_kernel falls through to
        # the wrong SM100 build.
        .apt_install("libnuma1")
        .pip_install("sglang[all]", "huggingface_hub[hf_transfer]")
        .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
    )
else:
    image = (
        modal.Image.debian_slim(python_version="3.11")
        .pip_install("vllm", "huggingface_hub[hf_transfer]")
        .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
    )

# ── Serve command — built at deploy time ──────────────────────────────────────
# SERVE_PROFILE / SERVE_MODEL are read from the local env at `modal deploy` time.
# The container re-imports this module without those vars, so all decisions must
# be baked here at module level — never branched on inside serve().

if SERVE_PROFILE == "experiment":
    _cmd: list[str] = [
        "python3",
        "-m",
        "sglang.launch_server",
        "--model",
        MODEL_ID,
        "--download-dir",
        "/model-cache",
        "--served-model-name",
        SERVED_MODEL_NAME,
        "--host",
        "0.0.0.0",  # noqa: S104
        "--port",
        "8000",
        "--context-length",
        str(CONTEXT_LENGTH),
        "--trust-remote-code",
    ]
    if TP_SIZE > 1:
        _cmd += ["--tensor-parallel-size", str(TP_SIZE)]
    if TOOL_CALL_PARSER:
        _cmd += ["--tool-call-parser", TOOL_CALL_PARSER]
    # CUDA graph capture triggers additional JIT compilation — disable on Modal.
    _cmd += ["--disable-cuda-graph"]
else:
    _cmd = [
        "python3",
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        MODEL_ID,
        "--download-dir",
        "/model-cache",
        "--served-model-name",
        SERVED_MODEL_NAME,
        "--host",
        "0.0.0.0",  # noqa: S104
        "--port",
        "8000",
        "--max-model-len",
        str(CONTEXT_LENGTH),
        "--trust-remote-code",
    ]
    if TP_SIZE > 1:
        _cmd += ["--tensor-parallel-size", str(TP_SIZE)]
    if TOOL_CALL_PARSER:
        _cmd += ["--enable-auto-tool-choice", "--tool-call-parser", TOOL_CALL_PARSER]

# ── Serve function ────────────────────────────────────────────────────────────


@app.function(
    image=image,
    gpu=GPU,
    secrets=[
        modal.Secret.from_dict(
            {
                "HF_TOKEN": os.environ["HF_TOKEN"],
                # Bake SERVE_PROFILE into the container so module-level branches
                # resolve correctly when the container re-imports this file.
                "SERVE_PROFILE": SERVE_PROFILE,
            }
        )
    ],
    timeout=60 * 60,
    scaledown_window=SCALEDOWN_WINDOW,
    volumes={"/model-cache": model_volume},
)
@modal.concurrent(max_inputs=32)
@modal.web_server(port=8000, startup_timeout=STARTUP_TIMEOUT)
def serve() -> None:
    """Start the inference server inside the Modal container."""
    subprocess.Popen(_cmd)  # noqa: S603
