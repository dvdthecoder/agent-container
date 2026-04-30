"""Non-interactive aider runner.

Usage: python3 aider_runner.py <task>

Environment variables read:
  OPENAI_BASE_URL  — vLLM endpoint, with /v1 suffix (e.g. https://host/v1)
  OPENAI_API_KEY   — API key (any non-empty string for self-hosted)
  OPENCODE_MODEL   — model name as served by vLLM (e.g. qwen2.5-coder)
  OPENCODE_WORKDIR — workspace directory inside the sandbox (default: /workspace)

Note: do NOT pass --openai-api-base to aider.  aider converts that flag into
os.environ["OPENAI_API_BASE"], which litellm handles differently from
OPENAI_BASE_URL — it strips the /v1 suffix, causing requests to land at
/chat/completions instead of /v1/chat/completions (404).
OPENAI_BASE_URL is already set in the container env and is read correctly
by the OpenAI SDK without any path mangling.
"""

from __future__ import annotations

import os
import subprocess
import sys

TASK = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
API_KEY = os.environ.get("OPENAI_API_KEY", "modal")
MODEL = os.environ.get("OPENCODE_MODEL", "")
WORKDIR = os.environ.get("OPENCODE_WORKDIR", "/workspace")

# OPENAI_BASE_URL in the container env may not have the /v1 suffix (the .env
# convention stores the bare host).  The OpenAI SDK requires /v1 in the base
# URL; without it litellm calls .../chat/completions instead of
# .../v1/chat/completions and gets a 404.
# Normalise here so the subprocess inherits the correct value.
_raw_base = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
BASE_URL = _raw_base if _raw_base.endswith("/v1") else f"{_raw_base}/v1" if _raw_base else ""

if not TASK:
    print("Usage: aider_runner.py <task>", file=sys.stderr)
    sys.exit(1)

# litellm requires the openai/ provider prefix to route via the OpenAI
# provider — it strips the prefix before sending to the API, so vLLM
# receives the clean model name.  Without it litellm errors:
# "LLM Provider NOT provided".
model_arg = f"openai/{MODEL}" if MODEL and "/" not in MODEL else MODEL or "openai/unknown"

print(
    f"[aider] task={TASK!r}  model={model_arg}  workdir={WORKDIR}  base_url={BASE_URL}",
    file=sys.stderr,
)

# Git identity must be set before aider runs — without it, aider's commit
# step fails/hangs even with --yes.
subprocess.run(["git", "config", "user.email", "agent@agent-container"], cwd=WORKDIR)  # noqa: S603
subprocess.run(["git", "config", "user.name", "Agent Container"], cwd=WORKDIR)  # noqa: S603

cmd = [
    "aider",
    "--yes",  # accept all changes without prompting
    "--map-tokens",
    "0",  # disable repo map — avoids silent multi-minute scan on fresh clone
    "--no-pretty",  # plain-text output; avoids terminal escape codes in sandbox streams
    "--model",
    model_arg,
    "--openai-api-key",
    API_KEY,
    "--message",
    TASK,
    # No --openai-api-base — OPENAI_BASE_URL env var is already set in the
    # container and is read correctly by the OpenAI SDK.
]

# Run from WORKDIR so aider picks up the git repo there.
# Pass corrected OPENAI_BASE_URL (with /v1) explicitly so aider's OpenAI SDK
# client hits the right endpoint.  Do NOT rely on inherited env — the .env
# convention stores the bare host without /v1.
env = {**os.environ, "OPENAI_BASE_URL": BASE_URL} if BASE_URL else None
sys.exit(subprocess.run(cmd, cwd=WORKDIR, env=env).returncode)  # noqa: S603
