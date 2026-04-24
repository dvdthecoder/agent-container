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
    output = proc.stdout.read()
    proc.wait()
    return output, proc.returncode
