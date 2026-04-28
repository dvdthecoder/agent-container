"""Execute a coding agent backend inside a Modal sandbox workspace."""

from __future__ import annotations

import modal
from agent.backends import AgentBackend


def run_agent(
    sb: modal.Sandbox,
    backend: AgentBackend,
    task: str,
    workdir: str = "/workspace",
) -> tuple[str, int]:
    """Run *backend* with *task* inside *sb*. Returns ``(stdout, exit_code)``.

    The caller is responsible for interpreting the exit code — a non-zero
    value means the agent reported failure but does not necessarily mean no
    useful output was produced.
    """
    cmd = backend.command(task)
    proc = sb.exec(*cmd, workdir=workdir)
    stdout = proc.stdout.read()
    stderr = proc.stderr.read()
    proc.wait()
    # Combine stdout + stderr so callers (and AgentTaskResult.error) surface
    # any failure messages written to stderr (e.g. opencode_runner.py errors).
    output = stdout
    if stderr:
        output = f"{stdout}\n[stderr]\n{stderr}".strip() if stdout else f"[stderr]\n{stderr}"
    return output, proc.returncode
