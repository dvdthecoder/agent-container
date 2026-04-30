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

# litellm requires the openai/ provider prefix to route via the OpenAI
# provider — it strips the prefix before sending to the API, so vLLM
# receives the clean model name.  Without it litellm errors immediately:
# "LLM Provider NOT provided".
model_arg = f"openai/{MODEL}" if MODEL and "/" not in MODEL else MODEL or "openai/unknown"

print(
    f"[aider] task={TASK!r}  model={model_arg}  base_url={BASE_URL}  workdir={WORKDIR}",
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
    "--openai-api-base",
    BASE_URL,
    "--openai-api-key",
    API_KEY,
    "--message",
    TASK,
]

# Run from WORKDIR so aider picks up the git repo there.
# Do NOT use --no-git + directory: aider rejects directories without git.
sys.exit(subprocess.run(cmd, cwd=WORKDIR).returncode)  # noqa: S603
