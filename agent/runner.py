"""Execute a coding agent backend inside a Modal sandbox workspace."""

from __future__ import annotations

import sys
import threading

import modal
from agent.backends import AgentBackend


def run_agent(
    sb: modal.Sandbox,
    backend: AgentBackend,
    task: str,
    workdir: str = "/workspace",
) -> tuple[str, int]:
    """Run *backend* with *task* inside *sb*. Returns ``(combined_output, exit_code)``.

    stdout and stderr are streamed to the local terminal in real time so
    failures during long runs are visible before the sandbox times out.
    The combined output is also returned for AgentTaskResult.
    """
    cmd = backend.command(task)
    proc = sb.exec(*cmd, workdir=workdir)

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def _stream(source, sink: list[str], label: str) -> None:
        for raw in source:
            line = raw.decode("utf-8", errors="replace")
            sink.append(line)
            sys.stderr.write(f"[sandbox:{label}] {line}")
            sys.stderr.flush()

    t_out = threading.Thread(
        target=_stream, args=(proc.stdout, stdout_lines, "stdout"), daemon=True
    )
    t_err = threading.Thread(
        target=_stream, args=(proc.stderr, stderr_lines, "stderr"), daemon=True
    )
    t_out.start()
    t_err.start()
    t_out.join()
    t_err.join()
    proc.wait()

    stdout = "".join(stdout_lines).rstrip()
    stderr = "".join(stderr_lines).rstrip()
    output = stdout
    if stderr:
        output = f"{stdout}\n[stderr]\n{stderr}".strip() if stdout else f"[stderr]\n{stderr}"
    return output, proc.returncode
