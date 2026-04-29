"""Non-interactive aider runner.

Usage: python3 aider_runner.py <task>

Environment variables read:
  OPENAI_BASE_URL  — vLLM endpoint (no /v1 suffix)
  OPENAI_API_KEY   — API key (any non-empty string for self-hosted)
  OPENCODE_MODEL   — model name as served by vLLM (e.g. qwen2.5-coder)
  OPENCODE_WORKDIR — workspace directory inside the sandbox (default: /workspace)
"""

from __future__ import annotations

import os
import subprocess
import sys

TASK = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
BASE_URL = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
API_KEY = os.environ.get("OPENAI_API_KEY", "modal")
MODEL = os.environ.get("OPENCODE_MODEL", "")
WORKDIR = os.environ.get("OPENCODE_WORKDIR", "/workspace")

if not TASK:
    print("Usage: aider_runner.py <task>", file=sys.stderr)
    sys.exit(1)

# aider expects base_url with /v1
if BASE_URL and not BASE_URL.endswith("/v1"):
    BASE_URL = f"{BASE_URL}/v1"

# aider requires the model to be prefixed with openai/ for custom endpoints
model_arg = f"openai/{MODEL}" if MODEL and "/" not in MODEL else MODEL or "openai/unknown"

print(
    f"[aider] task={TASK!r}  model={model_arg}  base_url={BASE_URL}  workdir={WORKDIR}",
    file=sys.stderr,
)

cmd = [
    "aider",
    "--yes",  # accept all changes without prompting
    "--no-git",  # sandbox handles git; aider only edits files
    "--model",
    model_arg,
    "--openai-api-base",
    BASE_URL,
    "--openai-api-key",
    API_KEY,
    "--message",
    TASK,
    WORKDIR,
]

sys.exit(subprocess.run(cmd).returncode)  # noqa: S603
