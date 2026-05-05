"""Deploy a coding model on Modal GPU — OpenAI-compatible inference endpoint.

Two profiles
------------
prod        — vLLM engine (default). Select a model with SERVE_MODEL.
experiment  — SGLang engine, Qwen2.5-Coder 7B · A10G.
              Deploys to agent-container-serve-experiment (separate app,
              never disturbs the prod endpoint).

Model registry (prod only)
--------------------------
  SERVE_MODEL=qwen2.5-coder-32b   Qwen2.5-Coder 32B · A100 80GB  (default, reliable tool use)
  SERVE_MODEL=qwen3-coder         Qwen3-Coder 80B   · 2× A100 80GB
  SERVE_MODEL=qwen3-8b            Qwen3 8B           · A10G         (fast, cheap)
  SERVE_MODEL=qwen3-30b           Qwen3 30B-A3B MoE  · A100 40GB   (efficient MoE)
  SERVE_MODEL=gemma4-12b          Gemma 4 12B        · A10G         (Google, fast)
  SERVE_MODEL=gemma4-27b          Gemma 4 27B        · A100 40GB    (Google, quality)
  SERVE_MODEL=minimax-m2.5        MiniMax M2.5 MoE   · 8× A100 80GB

Usage
-----
modal deploy modal/serve.py                                        # prod default
SERVE_MODEL=qwen3-8b    modal deploy modal/serve.py               # prod, Qwen3 8B
SERVE_MODEL=gemma4-27b  modal deploy modal/serve.py               # prod, Gemma 4 27B
SERVE_PROFILE=experiment modal deploy modal/serve.py              # SGLang experiment

After deployment Modal prints the endpoint URL:
  ✓ Created web function serve => https://your-org--agent-container-serve-serve.modal.run

Add it to .env (no /v1 suffix):
  OPENAI_BASE_URL=https://your-org--agent-container-serve-serve.modal.run
  OPENAI_API_KEY=modal
  OPENCODE_MODEL=<served_name from registry above>
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

# ── Profile ───────────────────────────────────────────────────────────────────
# "prod" (vLLM) is the default.  "experiment" uses SGLang and deploys to its
# own Modal app so the prod endpoint is never disturbed.

SERVE_PROFILE = os.environ.get("SERVE_PROFILE", "prod")

# ── Model registry (prod) ─────────────────────────────────────────────────────
# Add new models here.  All use vLLM; GPU, context, and tool-call parser are
# declared per-model so nothing needs changing outside this dict.
#
# tool_call_parser:
#   hermes    — Qwen/Mistral/Llama families (ChatML/hermes tool format)
#   pythonic  — Gemma 4 (Google's pythonic tool format)
#   Verify against vLLM docs when adding a new model family.

_PROD_MODELS: dict[str, dict] = {
    # ── Qwen2.5-Coder ─────────────────────────────────────────────────────────
    "qwen2.5-coder-32b": {
        # Proven default: reliable tool use with opencode, ~3 min cold start.
        "model_id": "Qwen/Qwen2.5-Coder-32B-Instruct",
        "served_name": "qwen2.5-coder-32b",
        "gpu": "A100-80GB",
        "context_length": 32_768,
        "tp_size": 1,
        "tool_call_parser": "hermes",
        "startup_timeout": 600,
    },
    # ── Qwen3-Coder ───────────────────────────────────────────────────────────
    "qwen3-coder": {
        "model_id": "Qwen/Qwen3-Coder-80B-Instruct",
        "served_name": "qwen3-coder",
        "gpu": "A100-80GB:2",
        "context_length": 131_072,
        "tp_size": 2,
        "tool_call_parser": "hermes",
        "startup_timeout": 600,
    },
    # ── Qwen3 general ─────────────────────────────────────────────────────────
    "qwen3-8b": {
        # Fast + cheap — good for simple tasks; fits comfortably on A10G (24 GB).
        "model_id": "Qwen/Qwen3-8B-Instruct",
        "served_name": "qwen3-8b",
        "gpu": "A10G",
        "context_length": 32_768,
        "tp_size": 1,
        "tool_call_parser": "hermes",
        "startup_timeout": 300,
    },
    "qwen3-30b": {
        # MoE: 30 B total / ~3 B active — efficient throughput on A100 40 GB.
        # Note: vLLM loads all expert weights; verify VRAM headroom before deploying.
        "model_id": "Qwen/Qwen3-30B-A3B-Instruct",
        "served_name": "qwen3-30b",
        "gpu": "A100-40GB",
        "context_length": 32_768,
        "tp_size": 1,
        "tool_call_parser": "hermes",
        "startup_timeout": 600,
    },
    # ── Gemma 4 (Google) ──────────────────────────────────────────────────────
    "gemma4-12b": {
        # Fits on A10G (24 GB); strong coding benchmarks for its size.
        "model_id": "google/gemma-4-12b-it",
        "served_name": "gemma4-12b",
        "gpu": "A10G",
        "context_length": 32_768,
        "tp_size": 1,
        "tool_call_parser": "pythonic",
        "startup_timeout": 300,
    },
    "gemma4-27b": {
        # Better quality than 12B; A100 40 GB gives comfortable VRAM headroom.
        "model_id": "google/gemma-4-27b-it",
        "served_name": "gemma4-27b",
        "gpu": "A100-40GB",
        "context_length": 32_768,
        "tp_size": 1,
        "tool_call_parser": "pythonic",
        "startup_timeout": 600,
    },
    # ── MiniMax ───────────────────────────────────────────────────────────────
    "minimax-m2.5": {
        # MoE, 456B total / ~45B active, Lightning Attention (1 M ctx).
        "model_id": "MiniMaxAI/MiniMax-M2.5",
        "served_name": "minimax-m2.5",
        "gpu": "A100-80GB:8",
        "context_length": 1_000_000,
        "tp_size": 8,
        "tool_call_parser": "hermes",
        "startup_timeout": 600,
    },
}

# Default model when SERVE_MODEL is not set.
_PROD_DEFAULT = "qwen2.5-coder-32b"

# ── Resolve configuration variables ──────────────────────────────────────────

if SERVE_PROFILE == "experiment":
    # SGLang engine — validated on Qwen2.5-Coder 7B + A10G with hermes parser.
    # Deploys to a separate Modal app so the prod endpoint is never disturbed.
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
    # prod — vLLM, model selected via SERVE_MODEL (default: qwen2.5-coder-32b).
    _model_key = os.environ.get("SERVE_MODEL", _PROD_DEFAULT)
    if _model_key not in _PROD_MODELS:
        raise ValueError(f"Unknown SERVE_MODEL={_model_key!r}. Available: {list(_PROD_MODELS)}")
    _m = _PROD_MODELS[_model_key]
    MODEL_ID: str = _m["model_id"]
    SERVED_MODEL_NAME: str = _m["served_name"]
    GPU: str | modal.gpu.A100 = _m["gpu"]
    CONTEXT_LENGTH: int = _m["context_length"]
    TP_SIZE: int = _m["tp_size"]
    TOOL_CALL_PARSER: str = _m.get("tool_call_parser", "hermes")
    SCALEDOWN_WINDOW = 600
    STARTUP_TIMEOUT: int = _m.get("startup_timeout", 600)

# ── Modal app ────────────────────────────────────────────────────────────────

# App names always include the model slug so the URL is self-describing and
# multiple models can run simultaneously without overwriting each other.
#
# Naming:
#   prod                → agent-container-serve-{model_slug}
#                         e.g. agent-container-serve-qwen2-5-coder-32b
#   SERVE_PROFILE=experiment → agent-container-serve-experiment (unchanged)

if SERVE_PROFILE == "experiment":
    _APP_NAME = "agent-container-serve-experiment"
else:
    _model_slug = _model_key.replace(".", "-")
    _APP_NAME = f"agent-container-serve-{_model_slug}"

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
        .pip_install("vllm==0.8.5", "huggingface_hub[hf_transfer]")
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
                # Bake SERVE_PROFILE and SERVE_MODEL into the container so
                # module-level branches resolve correctly when the container
                # re-imports this file.  Without SERVE_MODEL the container
                # always falls back to the _PROD_DEFAULT regardless of which
                # model was selected at deploy time.
                "SERVE_PROFILE": SERVE_PROFILE,
                "SERVE_MODEL": os.environ.get("SERVE_MODEL", _PROD_DEFAULT),
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
