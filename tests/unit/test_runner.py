"""Unit tests for agent.runner — Modal sandbox fully mocked."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.backends.claude_code import ClaudeCodeBackend
from agent.backends.gemini import GeminiBackend
from agent.backends.opencode import OpenCodeBackend
from agent.backends.stub import StubBackend
from agent.runner import run_agent


def _make_proc(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.stdout.read.return_value = stdout
    proc.stderr.read.return_value = stderr
    proc.returncode = returncode
    proc.wait.return_value = returncode
    return proc


def _make_sb(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    sb = MagicMock()
    sb.exec.return_value = _make_proc(stdout=stdout, stderr=stderr, returncode=returncode)
    return sb


# ------------------------------------------------------------------ basic contract


def test_returns_stdout_and_exit_code():
    sb = _make_sb(stdout="agent output", returncode=0)
    output, code = run_agent(sb, OpenCodeBackend(), "fix it")
    assert output == "agent output"
    assert code == 0


def test_non_zero_exit_code_returned_as_is():
    sb = _make_sb(stdout="error detail", returncode=1)
    output, code = run_agent(sb, OpenCodeBackend(), "fix it")
    assert code == 1
    assert output == "error detail"


def test_stderr_appended_when_stdout_present():
    proc = _make_proc(stdout="progress", stderr="[opencode] unexpected stop: timeout")
    sb = MagicMock()
    sb.exec.return_value = proc
    output, _ = run_agent(sb, OpenCodeBackend(), "fix it")
    assert "progress" in output
    assert "[stderr]" in output
    assert "unexpected stop" in output


def test_stderr_only_no_stdout():
    proc = _make_proc(stdout="", stderr="fatal error")
    sb = MagicMock()
    sb.exec.return_value = proc
    output, _ = run_agent(sb, OpenCodeBackend(), "fix it")
    assert "[stderr]" in output
    assert "fatal error" in output


def test_no_stderr_output_unchanged():
    proc = _make_proc(stdout="done", stderr="")
    sb = MagicMock()
    sb.exec.return_value = proc
    output, _ = run_agent(sb, OpenCodeBackend(), "fix it")
    assert output == "done"
    assert "[stderr]" not in output


# ------------------------------------------------------------------ command dispatch


@pytest.mark.parametrize(
    "backend, expected_argv_start",
    [
        (OpenCodeBackend(), ["python3", "/opencode_runner.py"]),
        (ClaudeCodeBackend(), ["claude", "--print"]),
        (GeminiBackend(), ["gemini", "--yolo", "-p"]),
        (StubBackend(), ["sh", "-c"]),
    ],
)
def test_exec_called_with_backend_command(backend, expected_argv_start):
    sb = _make_sb()
    run_agent(sb, backend, "my task")

    call_args = sb.exec.call_args
    argv = list(call_args[0])
    assert argv[: len(expected_argv_start)] == expected_argv_start


def test_task_passed_to_command():
    sb = _make_sb()
    run_agent(sb, OpenCodeBackend(), "add rate limiting")
    argv = list(sb.exec.call_args[0])
    assert "add rate limiting" in argv


# ------------------------------------------------------------------ workdir


def test_default_workdir_is_workspace():
    sb = _make_sb()
    run_agent(sb, OpenCodeBackend(), "task")
    _, kwargs = sb.exec.call_args
    assert kwargs.get("workdir") == "/workspace"


def test_custom_workdir_is_forwarded():
    sb = _make_sb()
    run_agent(sb, OpenCodeBackend(), "task", workdir="/custom")
    _, kwargs = sb.exec.call_args
    assert kwargs.get("workdir") == "/custom"
