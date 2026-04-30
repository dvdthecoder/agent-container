"""Unit tests for agent.git_ops — all git operations run inside a mocked sandbox."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.git_ops import branch_name, clone, collect_diff, push_and_pr
from sandbox.config import ConfigError, SandboxConfig


def _make_proc(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.stdout.read.return_value = stdout
    proc.stderr.read.return_value = stderr
    proc.returncode = returncode
    proc.wait.return_value = returncode
    return proc


# ------------------------------------------------------------------ branch_name


def test_branch_name_format():
    br = branch_name("opencode")
    assert br.startswith("agent/opencode-")
    ts = br[len("agent/opencode-") :]
    assert len(ts) == 15  # YYYYMMDD-HHMMSS
    assert ts[8] == "-"


def test_branch_name_uses_backend():
    assert branch_name("claude").startswith("agent/claude-")
    assert branch_name("gemini").startswith("agent/gemini-")


# ------------------------------------------------------------------ clone


def test_clone_success():
    sb = MagicMock()
    sb.exec.return_value = _make_proc(returncode=0)

    clone(sb, "https://github.com/org/repo", "main")

    # First call: git clone; second call: write .git/info/exclude
    assert sb.exec.call_count == 2
    first_call = sb.exec.call_args_list[0]
    assert first_call == (
        (
            "git",
            "clone",
            "--branch",
            "main",
            "--depth",
            "1",
            "https://github.com/org/repo",
            "/workspace",
        ),
        {},
    )


def test_clone_failure_raises_config_error():
    sb = MagicMock()
    sb.exec.return_value = _make_proc(stderr="fatal: not found", returncode=1)

    with pytest.raises(ConfigError, match="git clone failed"):
        clone(sb, "https://github.com/org/repo", "main")


# ------------------------------------------------------------------ collect_diff


def test_collect_diff_returns_diff_and_stat():
    sb = MagicMock()
    diff_proc = _make_proc(stdout="diff --git a/f b/f\n+fix")
    stat_proc = _make_proc(stdout=" 1 file changed, 1 insertion(+)\n")
    sb.exec.side_effect = [diff_proc, stat_proc]

    diff, stat = collect_diff(sb)

    assert diff == "diff --git a/f b/f\n+fix"
    assert stat == "1 file changed, 1 insertion(+)"  # stripped


def test_collect_diff_empty_when_no_changes():
    sb = MagicMock()
    sb.exec.return_value = _make_proc(stdout="")

    diff, stat = collect_diff(sb)

    assert diff == ""
    assert stat == ""


# ------------------------------------------------------------------ push_and_pr


def _pr_config(token: str = "ghp_test") -> SandboxConfig:
    return SandboxConfig(github_token=token)


def _pr_procs(pr_response: str = '{"html_url": "https://github.com/org/repo/pull/1"}'):
    """Return the standard sequence of mock procs for a successful push_and_pr call."""
    git_procs = [_make_proc() for _ in range(6)]  # config×2, checkout, add, commit, remote
    push_proc = _make_proc(returncode=0)
    write_proc = _make_proc()
    curl_proc = _make_proc(stdout=pr_response)
    return [*git_procs, push_proc, write_proc, curl_proc]


def test_push_and_pr_returns_branch_and_pr_url():
    sb = MagicMock()
    sb.exec.side_effect = _pr_procs()

    br, pr_url = push_and_pr(
        sb,
        repo="https://github.com/org/repo",
        base_branch="main",
        backend="opencode",
        task="fix the bug",
        config=_pr_config(),
    )

    assert br.startswith("agent/opencode-")
    assert pr_url == "https://github.com/org/repo/pull/1"


def test_push_and_pr_skips_when_no_token():
    sb = MagicMock()

    br, pr_url = push_and_pr(
        sb,
        repo="https://github.com/org/repo",
        base_branch="main",
        backend="opencode",
        task="fix the bug",
        config=SandboxConfig(),  # no github_token
    )

    assert br.startswith("agent/opencode-")
    assert pr_url is None
    sb.exec.assert_not_called()


def test_push_and_pr_skips_for_unsupported_host():
    sb = MagicMock()

    br, pr_url = push_and_pr(
        sb,
        repo="https://bitbucket.org/org/repo",
        base_branch="main",
        backend="opencode",
        task="fix the bug",
        config=_pr_config(),
    )

    assert pr_url is None
    sb.exec.assert_not_called()


def test_push_failure_raises_config_error():
    sb = MagicMock()
    git_procs = [_make_proc() for _ in range(6)]
    push_proc = _make_proc(stderr="remote: Permission denied", returncode=1)
    sb.exec.side_effect = [*git_procs, push_proc]

    with pytest.raises(ConfigError, match="git push failed"):
        push_and_pr(
            sb,
            repo="https://github.com/org/repo",
            base_branch="main",
            backend="opencode",
            task="fix the bug",
            config=_pr_config(),
        )


def test_gitlab_push_and_pr():
    sb = MagicMock()
    sb.exec.side_effect = _pr_procs(
        pr_response='{"web_url": "https://gitlab.com/org/repo/-/merge_requests/5"}'
    )

    br, pr_url = push_and_pr(
        sb,
        repo="https://gitlab.com/org/repo",
        base_branch="main",
        backend="opencode",
        task="fix the bug",
        config=SandboxConfig(gitlab_token="glpat_test"),
    )

    assert pr_url == "https://gitlab.com/org/repo/-/merge_requests/5"
