"""Execute a coding agent backend inside a Modal sandbox workspace."""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
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

# Print a heartbeat line when the agent has been silent for this long.
# Suppressed immediately when output resumes — avoids spamming the terminal
# when aider/opencode is actively writing.
_HEARTBEAT_INTERVAL = 30.0


def run_agent(
    sb: modal.Sandbox,
    backend: AgentBackend,
    task: str,
    workdir: str = "/workspace",
    logger: RunLogger | None = None,
    timeout: float | None = None,
    on_log: Callable[[str, str], None] | None = None,
) -> tuple[str, int]:
    """Run *backend* with *task* inside *sb*. Returns ``(combined_output, exit_code)``.

    stdout and stderr are streamed to the local terminal in real time so
    failures during long runs are visible before the sandbox times out.
    Each line is also persisted to *logger* when provided.

    *on_log(label, line)* is called for every line as it arrives — use this
    to forward live output to the dashboard without waiting for the run to finish.

    A heartbeat line is printed to stderr every 30 s when the agent is silent
    (e.g. the LLM is generating but not yet writing output), so the terminal
    never goes completely dark during a long RUNNING phase.

    If *timeout* seconds elapse before the process finishes, the sandbox
    is terminated immediately and a non-zero exit code is returned.
    """
    cmd = backend.command(task)
    proc = sb.exec(*cmd, workdir=workdir)

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    # _last_output_time[0] is updated by _stream threads on every line.
    # Single-element list so daemon threads can mutate it without a Lock
    # (float assignment is atomic under the GIL).
    _last_output_time: list[float] = [time.monotonic()]
    _heartbeat_stop = threading.Event()
    _run_start = time.monotonic()

    def _stream(source, sink: list[str], label: str) -> None:
        try:
            for raw in source:
                line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
                sink.append(line)
                _last_output_time[0] = time.monotonic()
                sys.stderr.write(f"[sandbox:{label}] {line}")
                sys.stderr.flush()
                if logger is not None:
                    level = "error" if label == "stderr" else "info"
                    logger.log(f"sandbox:{label}", line.rstrip(), level=level)
                if on_log is not None:
                    try:
                        on_log(label, line.rstrip())
                    except Exception:  # noqa: BLE001, S110
                        pass
        except Exception as exc:  # noqa: S110
            # Sandbox killed mid-stream — log and exit thread cleanly.
            msg = f"[sandbox:{label}] stream closed: {exc}"
            sys.stderr.write(msg + "\n")
            if logger is not None:
                logger.log(f"sandbox:{label}", msg, level="warn")

    def _heartbeat() -> None:
        """Print a status line when the agent has been silent for _HEARTBEAT_INTERVAL s."""
        tick = min(1.0, _HEARTBEAT_INTERVAL / 5)
        while not _heartbeat_stop.wait(timeout=tick):
            elapsed_total = time.monotonic() - _run_start
            silent_for = time.monotonic() - _last_output_time[0]
            if silent_for >= _HEARTBEAT_INTERVAL:
                msg = (
                    f"[runner] still running  elapsed={elapsed_total:.0f}s"
                    f"  (no output for {silent_for:.0f}s)"
                )
                sys.stderr.write(msg + "\n")
                sys.stderr.flush()
                if logger is not None:
                    logger.log("runner", msg.strip())
                # Reset so we don't spam every second after the threshold.
                _last_output_time[0] = time.monotonic()

    t_out = threading.Thread(
        target=_stream, args=(proc.stdout, stdout_lines, "stdout"), daemon=True
    )
    t_err = threading.Thread(
        target=_stream, args=(proc.stderr, stderr_lines, "stderr"), daemon=True
    )
    t_hb = threading.Thread(target=_heartbeat, daemon=True)
    t_out.start()
    t_err.start()
    t_hb.start()

    if timeout is not None:
        # Wait for streams to finish within the budget; terminate if exceeded.
        t_out.join(timeout=timeout)
        t_err.join(timeout=_STREAM_JOIN_TIMEOUT)
        if t_out.is_alive():
            msg = f"[runner] agent timeout after {timeout:.0f}s — terminating sandbox"
            sys.stderr.write(msg + "\n")
            if logger is not None:
                logger.log("runner", msg, level="error")
            _heartbeat_stop.set()
            try:
                sb.terminate()
            except Exception:  # noqa: BLE001, S110
                pass
            # Raise instead of returning — sandbox.py must not attempt collect_diff
            # or any further exec on a terminated container (those calls block).
            raise TimeoutError(msg)
    else:
        t_out.join()
        t_err.join(timeout=_STREAM_JOIN_TIMEOUT)

    _heartbeat_stop.set()
    proc.wait()

    stdout = "".join(stdout_lines).rstrip()
    stderr = "".join(stderr_lines).rstrip()
    output = stdout
    if stderr:
        output = f"{stdout}\n[stderr]\n{stderr}".strip() if stdout else f"[stderr]\n{stderr}"
    return output, proc.returncode
