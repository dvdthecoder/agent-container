"""Non-interactive aider runner.

Usage: python3 aider_runner.py <task>

Environment variables read:
  OPENAI_BASE_URL  — vLLM endpoint, already normalised to include /v1
                     (e.g. https://host/v1).  Set by SandboxConfig.env_for_backend
                     before the container starts — do not normalise here.
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
BASE_URL = os.environ.get("OPENAI_BASE_URL", "")

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
    "1024",  # small map — gives model file context without expensive full scan
    "--no-pretty",  # plain-text output — avoids terminal escape codes in sandbox streams
    "--edit-format",
    "diff",  # unified diff format — more robust than whole-file when model adds preamble text
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
# OPENAI_BASE_URL is pre-normalised (includes /v1) by SandboxConfig.env_for_backend
# before the container starts — no further manipulation needed here.
sys.exit(subprocess.run(cmd, cwd=WORKDIR).returncode)  # noqa: S603
