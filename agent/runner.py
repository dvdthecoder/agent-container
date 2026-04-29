"""Execute a coding agent backend inside a Modal sandbox workspace."""

from __future__ import annotations

import sys
import threading
from typing import TYPE_CHECKING

import modal
from agent.backends import AgentBackend

if TYPE_CHECKING:
    from agent.log_store import RunLogger

# How long (seconds) to wait for streaming threads to finish after the
# sandbox process exits.  If the sandbox is killed by Modal (timeout),
# the iterator may never StopIterate — daemon threads will die with the
# process, but join() would block forever.  This cap prevents that.
_STREAM_JOIN_TIMEOUT = 10.0


def run_agent(
    sb: modal.Sandbox,
    backend: AgentBackend,
    task: str,
    workdir: str = "/workspace",
    logger: RunLogger | None = None,
    timeout: float | None = None,
) -> tuple[str, int]:
    """Run *backend* with *task* inside *sb*. Returns ``(combined_output, exit_code)``.

    stdout and stderr are streamed to the local terminal in real time so
    failures during long runs are visible before the sandbox times out.
    Each line is also persisted to *logger* when provided.

    If *timeout* seconds elapse before the process finishes, the sandbox
    is terminated immediately and a non-zero exit code is returned.
    """
    cmd = backend.command(task)
    proc = sb.exec(*cmd, workdir=workdir)

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def _stream(source, sink: list[str], label: str) -> None:
        try:
            for raw in source:
                line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
                sink.append(line)
                sys.stderr.write(f"[sandbox:{label}] {line}")
                sys.stderr.flush()
                if logger is not None:
                    level = "error" if label == "stderr" else "info"
                    logger.log(f"sandbox:{label}", line.rstrip(), level=level)
        except Exception as exc:  # noqa: S110
            # Sandbox killed mid-stream — log and exit thread cleanly.
            msg = f"[sandbox:{label}] stream closed: {exc}"
            sys.stderr.write(msg + "\n")
            if logger is not None:
                logger.log(f"sandbox:{label}", msg, level="warn")

    t_out = threading.Thread(
        target=_stream, args=(proc.stdout, stdout_lines, "stdout"), daemon=True
    )
    t_err = threading.Thread(
        target=_stream, args=(proc.stderr, stderr_lines, "stderr"), daemon=True
    )
    t_out.start()
    t_err.start()

    if timeout is not None:
        # Wait for streams to finish within the budget; terminate if exceeded.
        t_out.join(timeout=timeout)
        t_err.join(timeout=_STREAM_JOIN_TIMEOUT)
        if t_out.is_alive():
            msg = f"[runner] agent timeout after {timeout:.0f}s — terminating sandbox"
            sys.stderr.write(msg + "\n")
            if logger is not None:
                logger.log("runner", msg, level="error")
            try:
                sb.terminate()
            except Exception:  # noqa: S110
                pass
            return "".join(stdout_lines).rstrip(), 1
    else:
        t_out.join()
        t_err.join(timeout=_STREAM_JOIN_TIMEOUT)

    proc.wait()

    stdout = "".join(stdout_lines).rstrip()
    stderr = "".join(stderr_lines).rstrip()
    output = stdout
    if stderr:
        output = f"{stdout}\n[stderr]\n{stderr}".strip() if stdout else f"[stderr]\n{stderr}"
    return output, proc.returncode
