"""Unit tests for agent.tester — detection and result parsing."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.tester import _parse_counts, detect_and_run


def _make_proc(stdout: str = "", returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.stdout.read.return_value = stdout
    proc.returncode = returncode
    proc.wait.return_value = returncode
    return proc


def _make_sb(*procs: MagicMock) -> MagicMock:
    """Return a sandbox mock whose exec calls return *procs* in order."""
    sb = MagicMock()
    sb.exec.side_effect = list(procs)
    return sb


# ------------------------------------------------------------------ no runner found


def test_returns_none_when_no_runner_detected():
    sb = _make_sb(_make_proc(stdout="none"))
    assert detect_and_run(sb) is None


# ------------------------------------------------------------------ pytest


def test_detects_pytest_and_runs():
    detect_proc = _make_proc(stdout="pytest")
    test_proc = _make_proc(stdout="3 passed in 0.5s", returncode=0)
    sb = _make_sb(detect_proc, test_proc)

    result = detect_and_run(sb)

    assert result is not None
    assert result.runner_name == "pytest"
    assert result.passed == 3
    assert result.failed == 0
    assert result.success is True


def test_pytest_with_failures():
    detect_proc = _make_proc(stdout="pytest")
    test_proc = _make_proc(stdout="2 passed, 1 failed in 0.8s", returncode=1)
    sb = _make_sb(detect_proc, test_proc)

    result = detect_and_run(sb)

    assert result.passed == 2
    assert result.failed == 1
    assert result.success is False


def test_pytest_output_captured():
    detect_proc = _make_proc(stdout="pytest")
    test_proc = _make_proc(stdout="FAILED test_foo.py::test_bar\n1 failed", returncode=1)
    sb = _make_sb(detect_proc, test_proc)

    result = detect_and_run(sb)

    assert "test_foo" in result.output


# ------------------------------------------------------------------ npm


def test_detects_npm_and_runs():
    detect_proc = _make_proc(stdout="npm")
    test_proc = _make_proc(stdout="Tests: 5 passed, 0 failed", returncode=0)
    sb = _make_sb(detect_proc, test_proc)

    result = detect_and_run(sb)

    assert result.runner_name == "npm"
    assert result.passed == 5
    assert result.success is True


# ------------------------------------------------------------------ cargo


def test_detects_cargo_and_parses():
    detect_proc = _make_proc(stdout="cargo")
    test_proc = _make_proc(
        stdout="test result: ok. 4 passed; 0 failed; 0 ignored",
        returncode=0,
    )
    sb = _make_sb(detect_proc, test_proc)

    result = detect_and_run(sb)

    assert result.runner_name == "cargo"
    assert result.passed == 4
    assert result.failed == 0


def test_cargo_with_failures():
    detect_proc = _make_proc(stdout="cargo")
    test_proc = _make_proc(
        stdout="test result: FAILED. 2 passed; 1 failed; 0 ignored",
        returncode=1,
    )
    sb = _make_sb(detect_proc, test_proc)

    result = detect_and_run(sb)
    assert result.failed == 1
    assert result.passed == 2


# ------------------------------------------------------------------ go


def test_detects_go_and_uses_exit_code():
    detect_proc = _make_proc(stdout="go")
    test_proc = _make_proc(stdout="ok  github.com/org/repo  0.003s", returncode=0)
    sb = _make_sb(detect_proc, test_proc)

    result = detect_and_run(sb)

    assert result.runner_name == "go"
    assert result.success is True


def test_go_failure_uses_exit_code():
    detect_proc = _make_proc(stdout="go")
    test_proc = _make_proc(stdout="FAIL\tgithub.com/org/repo", returncode=1)
    sb = _make_sb(detect_proc, test_proc)

    result = detect_and_run(sb)
    assert result.success is False


# ------------------------------------------------------------------ _parse_counts


@pytest.mark.parametrize(
    "runner, output, exit_code, expected",
    [
        ("pytest", "5 passed in 0.3s", 0, (5, 0)),
        ("pytest", "3 passed, 2 failed in 0.5s", 1, (3, 2)),
        ("pytest", "no output at all", 0, (1, 0)),
        ("pytest", "no output at all", 1, (0, 1)),
        ("npm", "Tests: 4 passed, 1 failed", 1, (4, 1)),
        ("cargo", "3 passed; 1 failed; 0 ignored", 1, (3, 1)),
        ("go", "ok  github.com/...", 0, (1, 0)),
        ("go", "FAIL\t...", 1, (0, 1)),
    ],
)
def test_parse_counts(runner, output, exit_code, expected):
    assert _parse_counts(runner, output, exit_code) == expected
