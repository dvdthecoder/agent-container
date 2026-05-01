"""Deploy a coding model on Modal GPU — OpenAI-compatible inference endpoint.

Inference engines
-----------------
vLLM    — default, stable tool calling, all profiles (test / prod / minimax)
SGLang  — Phase 3 verification profile only (sglang)

Profiles
--------
test    — Qwen2.5-Coder 7B   on A10G        (cheap, ~2 min cold start)  [vLLM]
prod    — Qwen3-Coder 80B    on 2× A100 80G (128k context)               [vLLM]
minimax — MiniMax M2.5       on 8× A100 80G (1M context, MoE)            [vLLM]
sglang  — Qwen2.5-Coder 7B   on A10G        (Phase 3 SGLang validation)  [SGLang]

Usage
-----
modal deploy modal/serve.py                          # test  (vLLM, Qwen2.5-Coder 7B)
SERVE_PROFILE=prod    modal deploy modal/serve.py    # prod  (vLLM, Qwen3-Coder 80B)
SERVE_PROFILE=minimax modal deploy modal/serve.py    # minimax (vLLM, MiniMax M2.5)
SERVE_PROFILE=sglang  modal deploy modal/serve.py    # sglang (SGLang, Qwen2.5-Coder 7B)

The sglang profile deploys to a separate Modal app (agent-container-serve-sglang) so
the vLLM endpoint is never disturbed. To run Phase 3 validation, point OPENAI_BASE_URL
at the SGLang endpoint URL and run: make example BACKEND=opencode

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

elif SERVE_PROFILE == "sglang":
    # Phase 3 — SGLang verification against same model/GPU as the test profile.
    # Deploys to a separate Modal app (agent-container-serve-sglang) so the
    # vLLM endpoint is untouched and both can run simultaneously.
    # Exit criteria: make example BACKEND=opencode produces a non-empty diff.
    # If tool calling still crashes, failure scopes entirely to SGLang — the
    # opencode proxy is clean (proven in Phase 2).
    MODEL_ID = "Qwen/Qwen2.5-Coder-7B-Instruct"
    SERVED_MODEL_NAME = "qwen2.5-coder"
    GPU = "A10G"
    CONTEXT_LENGTH = 32_768
    TP_SIZE = 1
    SCALEDOWN_WINDOW = 300
    STARTUP_TIMEOUT = 600  # torch_native + model download needs ~5-6 min cold start
    # SGLang tool-call parser for Qwen2.5 models.
    # In v0.4.7 this crashed the server on the first request with tool schemas.
    # Phase 3 validates whether the current SGLang image has fixed this.
    TOOL_CALL_PARSER = "qwen25"

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

# SGLang gets its own app name so both endpoints can be deployed simultaneously.
# vLLM profiles always deploy to agent-container-serve.
_APP_NAME = "agent-container-serve-sglang" if SERVE_PROFILE == "sglang" else "agent-container-serve"
app = modal.App(_APP_NAME)

# Persistent volume — model weights cached here, not re-downloaded on cold start.
# Both vLLM and SGLang profiles share the same volume so weights downloaded by
# one profile are reused by the other — no double download.
model_volume = modal.Volume.from_name("agent-container-models", create_if_missing=True)

# Image is selected per inference engine.
# SGLang and vLLM are mutually exclusive — installing both would bloat the image
# and risk version conflicts.  The sglang profile gets its own lean image.
if SERVE_PROFILE == "sglang":
    image = (
        modal.Image.debian_slim(python_version="3.11")
        .pip_install("sglang[all]", "huggingface_hub[hf_transfer]")
        .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
    )
else:
    # Build Modal-natively so Modal can fully manage the Python environment.
    # vllm/vllm-openai Docker image is not compatible with Modal's bootstrap
    # (ENTRYPOINT conflict, no python symlink, Python version undetectable).
    # Modal injects CUDA drivers at runtime when a GPU is attached — no CUDA
    # base image is required.  First build downloads vLLM wheels (~10 min);
    # subsequent deploys reuse the cached layer.
    image = (
        modal.Image.debian_slim(python_version="3.11")
        .pip_install("vllm", "huggingface_hub[hf_transfer]")
        .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
    )

# ── Serve command — built at deploy time, not at container runtime ────────────
#
# SERVE_PROFILE is read from the local env when `modal deploy` runs.
# The container re-imports this module WITHOUT the SERVE_PROFILE env var, so
# any branch on SERVE_PROFILE inside serve() would always fall through to the
# default ("test"/vLLM).  Fix: build the command list here at module level so
# it is fully determined at deploy time and baked into the deployment.

if SERVE_PROFILE == "sglang":
    # SGLang differences from vLLM:
    #   no --enable-auto-tool-choice (SGLang uses --tool-call-parser alone)
    #   --context-length instead of --max-model-len
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
    # Modal containers don't expose the CUDA toolkit (nvcc/headers).
    # FlashInfer and Triton both JIT-compile CUDA kernels at startup and
    # fail with "Could not find nvcc / CUDA installation".
    # torch_native is pure PyTorch — no compilation, no nvcc required.
    # Performance is reduced but acceptable for Phase 3 tool-call validation.
    _cmd += ["--attention-backend", "torch_native", "--disable-cuda-graph"]
else:
    # vLLM — default for test / prod / minimax profiles.
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
        "0.0.0.0",  # noqa: S104 — container-internal binding
        "--port",
        "8000",
        "--max-model-len",
        str(CONTEXT_LENGTH),
        "--trust-remote-code",
    ]
    if TP_SIZE > 1:
        _cmd += ["--tensor-parallel-size", str(TP_SIZE)]
    # Tool calling — required for opencode; not needed for aider (diff format).
    if TOOL_CALL_PARSER:
        _cmd += ["--enable-auto-tool-choice", "--tool-call-parser", TOOL_CALL_PARSER]

# ── Serve function ───────────────────────────────────────────────────────────


@app.function(
    image=image,
    gpu=GPU,
    secrets=[
        modal.Secret.from_dict(
            {
                "HF_TOKEN": os.environ["HF_TOKEN"],
                # Inject SERVE_PROFILE so the container sees it when re-importing
                # this module at runtime — without it the branch always defaults
                # to "test" and starts vLLM even on the sglang profile.
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
    """Start the inference server inside the Modal container.

    The command (_cmd) is built at deploy time so the correct engine
    (vLLM or SGLang) runs regardless of the container's env vars.
    """
    subprocess.Popen(_cmd)  # noqa: S603
