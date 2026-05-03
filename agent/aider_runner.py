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
import re
import subprocess
import sys
import threading

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

# ---------------------------------------------------------------------------
# Token tracking
# ---------------------------------------------------------------------------
# aider prints one line per model call to stderr:
#   Tokens: 2,841 sent, 381 received. Cost: $0.00 message, $0.00 session.
# We accumulate across all turns and emit a summary line that sandbox.py
# already knows how to parse: [runner] token_usage: prompt=X completion=Y total=Z
_TOKEN_RE = re.compile(r"Tokens:\s+([\d,]+)\s+sent,\s+([\d,]+)\s+received")

_prompt_tokens = 0
_completion_tokens = 0


def _stream(source, dest, is_stderr: bool) -> None:
    """Forward *source* lines to *dest*, parsing token lines when on stderr."""
    global _prompt_tokens, _completion_tokens  # noqa: PLW0603
    for raw in source:
        line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
        dest.write(line)
        dest.flush()
        if is_stderr:
            m = _TOKEN_RE.search(line)
            if m:
                _prompt_tokens += int(m.group(1).replace(",", ""))
                _completion_tokens += int(m.group(2).replace(",", ""))


# ---------------------------------------------------------------------------
# Run aider, stream output, emit token summary
# ---------------------------------------------------------------------------

# Run from WORKDIR so aider picks up the git repo there.
# OPENAI_BASE_URL is pre-normalised (includes /v1) by SandboxConfig.env_for_backend
# before the container starts — no further manipulation needed here.
proc = subprocess.Popen(  # noqa: S603
    cmd,
    cwd=WORKDIR,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)

t_out = threading.Thread(target=_stream, args=(proc.stdout, sys.stdout, False), daemon=True)
t_err = threading.Thread(target=_stream, args=(proc.stderr, sys.stderr, True), daemon=True)
t_out.start()
t_err.start()
t_out.join()
t_err.join()
proc.wait()

total = _prompt_tokens + _completion_tokens
print(
    f"[runner] token_usage: prompt={_prompt_tokens} completion={_completion_tokens} total={total}",
    file=sys.stderr,
    flush=True,
)

sys.exit(proc.returncode)
