"""Unit tests for ModalSandbox — Modal SDK fully mocked, no network required."""

from unittest.mock import MagicMock, patch

import pytest

from sandbox.config import SandboxConfig
from sandbox.sandbox import ModalSandbox
from sandbox.spec import AgentTaskSpec


def _spec(**kwargs) -> AgentTaskSpec:
    # run_tests=False keeps side_effect lists predictable — tester tests are in test_tester.py
    defaults = dict(repo="https://github.com/org/repo", task="fix it", run_tests=False)
    return AgentTaskSpec(**{**defaults, **kwargs})


def _config() -> SandboxConfig:
    return SandboxConfig()


@pytest.fixture(autouse=True)
def mock_app_lookup():
    """Prevent modal.App.lookup from hitting the Modal API in unit tests."""
    with patch("sandbox.sandbox.modal.App.lookup", return_value=MagicMock()):
        yield


@pytest.fixture(autouse=True)
def mock_run_logger():
    """Prevent RunLogger from writing to the real filesystem in unit tests."""
    with patch("sandbox.sandbox.RunLogger.create") as mock_cls:
        inst = MagicMock()
        inst.run_id = "run-test-abc123"
        mock_cls.return_value = inst
        yield inst


@pytest.fixture(autouse=True)
def mock_wait_for_inference():
    """Skip inference endpoint polling — no network in unit tests."""
    with patch("sandbox.sandbox._wait_for_inference"):
        yield


def _make_proc(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    # runner.py streams by iterating over proc.stdout/proc.stderr (bytes lines).
    stdout_bytes = [ln.encode() + b"\n" for ln in stdout.splitlines()] if stdout else []
    stderr_bytes = [ln.encode() + b"\n" for ln in stderr.splitlines()] if stderr else []
    proc.stdout.__iter__ = lambda self: iter(stdout_bytes)
    proc.stderr.__iter__ = lambda self: iter(stderr_bytes)
    # Keep .read() for git_ops / tester which still use it.
    proc.stdout.read.return_value = stdout
    proc.stderr.read.return_value = stderr
    proc.returncode = returncode
    proc.wait.return_value = returncode
    return proc


# ------------------------------------------------------------------ happy path


@patch("sandbox.sandbox.modal.Sandbox.create")
def test_run_success_returns_result(mock_create):
    sb = MagicMock()
    sb.object_id = "sb-abc123"
    mock_create.return_value = sb

    clone_proc = _make_proc(returncode=0)
    agent_proc = _make_proc(stdout="done", returncode=0)
    diff_proc = _make_proc(stdout="diff --git a/f b/f\n+fix")
    stat_proc = _make_proc(stdout=" 1 file changed")

    sb.exec.side_effect = [clone_proc, _make_proc(), agent_proc, diff_proc, stat_proc]

    # create_pr=False so the test doesn't need to mock PR exec calls.
    result = ModalSandbox(_config()).run(_spec(create_pr=False))

    assert result.success is True
    assert result.run_id == "run-test-abc123"
    assert result.diff == "diff --git a/f b/f\n+fix"
    assert result.diff_stat == "1 file changed"
    assert result.branch is None
    assert result.pr_url is None
    assert result.error is None
    assert result.backend == "opencode"
    sb.terminate.assert_called_once()


@patch("sandbox.sandbox.modal.Sandbox.create")
def test_run_creates_pr_when_agent_produces_diff(mock_create):
    sb = MagicMock()
    sb.object_id = "sb-pr"
    mock_create.return_value = sb

    clone_proc = _make_proc(returncode=0)
    agent_proc = _make_proc(stdout="done", returncode=0)
    diff_proc = _make_proc(stdout="diff --git a/f b/f\n+fix")
    stat_proc = _make_proc(stdout=" 1 file changed")
    # _push_and_pr: git config×2, checkout, add, commit, remote set-url, push
    pr_git_procs = [_make_proc() for _ in range(7)]
    write_payload_proc = _make_proc()
    curl_proc = _make_proc(stdout='{"html_url": "https://github.com/org/repo/pull/42"}')

    sb.exec.side_effect = [
        clone_proc,
        _make_proc(),  # .git/info/exclude
        agent_proc,
        diff_proc,
        stat_proc,
        *pr_git_procs,
        write_payload_proc,
        curl_proc,
    ]

    config = SandboxConfig(github_token="ghp_test")
    result = ModalSandbox(config).run(_spec(repo="https://github.com/org/repo", create_pr=True))

    assert result.success is True
    assert result.branch is not None
    assert result.branch.startswith("agent/opencode-")
    assert result.pr_url == "https://github.com/org/repo/pull/42"
    sb.terminate.assert_called_once()


@patch("sandbox.sandbox.modal.Sandbox.create")
def test_run_fails_when_no_diff(mock_create):
    """Empty diff after exit 0 is treated as failure — agent made no changes."""
    sb = MagicMock()
    mock_create.return_value = sb
    sb.exec.return_value = _make_proc()  # diff stdout="" → empty

    result = ModalSandbox(_config()).run(_spec(create_pr=True))

    assert result.success is False
    assert result.branch is None
    assert result.pr_url is None
    assert "no changes" in result.error


@patch("sandbox.sandbox.modal.Sandbox.create")
def test_run_skips_pr_when_create_pr_false(mock_create):
    sb = MagicMock()
    mock_create.return_value = sb
    clone_proc = _make_proc()
    agent_proc = _make_proc(stdout="done")
    diff_proc = _make_proc(stdout="diff --git a/f b/f\n+fix")
    stat_proc = _make_proc(stdout=" 1 file changed")
    sb.exec.side_effect = [clone_proc, _make_proc(), agent_proc, diff_proc, stat_proc]

    result = ModalSandbox(_config()).run(_spec(create_pr=False))

    assert result.branch is None
    assert result.pr_url is None


@patch("sandbox.sandbox.modal.Sandbox.create")
def test_run_skips_pr_when_no_github_token(mock_create):
    """No GITHUB_TOKEN — provider is detected but token check fails fast; no git ops run."""
    sb = MagicMock()
    sb.object_id = "sb-no-token"
    mock_create.return_value = sb

    clone_proc = _make_proc()
    agent_proc = _make_proc(stdout="done")
    diff_proc = _make_proc(stdout="diff --git a/f b/f\n+fix")
    stat_proc = _make_proc(stdout=" 1 file changed")
    # Provider is detected and token is missing before any git exec — only 4 calls total.
    sb.exec.side_effect = [clone_proc, _make_proc(), agent_proc, diff_proc, stat_proc]

    result = ModalSandbox(SandboxConfig()).run(
        _spec(repo="https://github.com/org/repo", create_pr=True)
    )

    assert result.success is True
    assert result.branch is not None  # branch name is still generated
    assert result.pr_url is None


@patch("sandbox.sandbox.modal.Sandbox.create")
def test_push_failure_returns_failed_result(mock_create):
    sb = MagicMock()
    sb.object_id = "sb-push-fail"
    mock_create.return_value = sb

    clone_proc = _make_proc()
    agent_proc = _make_proc(stdout="done")
    diff_proc = _make_proc(stdout="diff --git a/f b/f\n+fix")
    stat_proc = _make_proc(stdout=" 1 file changed")
    git_procs = [_make_proc() for _ in range(6)]
    push_proc = _make_proc(returncode=1)
    push_proc.stderr.read.return_value = "remote: Permission denied"
    sb.exec.side_effect = [
        clone_proc, _make_proc(), agent_proc, diff_proc, stat_proc, *git_procs, push_proc
    ]

    config = SandboxConfig(github_token="ghp_test")
    result = ModalSandbox(config).run(_spec(repo="https://github.com/org/repo", create_pr=True))

    assert result.success is False
    assert "git push failed" in result.error
    sb.terminate.assert_called_once()


@patch("sandbox.sandbox.modal.Sandbox.create")
def test_run_passes_timeout_to_sandbox(mock_create):
    sb = MagicMock()
    mock_create.return_value = sb
    sb.exec.return_value = _make_proc()

    ModalSandbox(_config()).run(_spec(timeout_seconds=120))

    _, kwargs = mock_create.call_args
    assert kwargs["timeout"] == 120


@patch("sandbox.sandbox.modal.Sandbox.create")
def test_run_uses_custom_image_when_spec_sets_it(mock_create):
    sb = MagicMock()
    mock_create.return_value = sb
    sb.exec.return_value = _make_proc()

    with patch("sandbox.sandbox.modal.Image.from_registry") as mock_img:
        mock_img.return_value = MagicMock()
        ModalSandbox(_config()).run(_spec(image="python:3.11-slim"))

    mock_img.assert_called_once_with("python:3.11-slim")


# ------------------------------------------------------------------ failures


@patch("sandbox.sandbox.modal.Sandbox.create")
def test_clone_failure_returns_failed_result(mock_create):
    sb = MagicMock()
    sb.object_id = "sb-clone-fail"
    mock_create.return_value = sb

    clone_proc = _make_proc(returncode=1)
    clone_proc.stderr.read.return_value = "fatal: repo not found"
    sb.exec.return_value = clone_proc

    result = ModalSandbox(_config()).run(_spec())

    assert result.success is False
    assert "git clone failed" in result.error
    sb.terminate.assert_called_once()


@patch("sandbox.sandbox.modal.Sandbox.create")
def test_agent_nonzero_exit_returns_failed_result(mock_create):
    sb = MagicMock()
    sb.object_id = "sb-agent-fail"
    mock_create.return_value = sb

    clone_proc = _make_proc(returncode=0)
    agent_proc = _make_proc(stdout="something went wrong", returncode=1)
    diff_proc = _make_proc()
    stat_proc = _make_proc()

    sb.exec.side_effect = [clone_proc, _make_proc(), agent_proc, diff_proc, stat_proc]

    result = ModalSandbox(_config()).run(_spec())

    assert result.success is False
    assert result.error == "something went wrong"
    sb.terminate.assert_called_once()


@patch("sandbox.sandbox.modal.Sandbox.create")
def test_terminate_called_even_when_exec_raises(mock_create):
    sb = MagicMock()
    sb.object_id = "sb-exc"
    mock_create.return_value = sb
    sb.exec.side_effect = RuntimeError("connection lost")

    result = ModalSandbox(_config()).run(_spec())

    assert result.success is False
    assert "connection lost" in result.error
    sb.terminate.assert_called_once()


@patch("sandbox.sandbox.modal.Sandbox.create")
def test_terminate_failure_does_not_mask_result(mock_create):
    sb = MagicMock()
    sb.object_id = "sb-term-fail"
    mock_create.return_value = sb
    diff_proc = _make_proc(stdout="diff --git a/f b/f\n+fix")
    stat_proc = _make_proc(stdout=" 1 file changed")
    sb.exec.side_effect = [
        _make_proc(), _make_proc(), _make_proc(stdout="done"), diff_proc, stat_proc
    ]
    sb.terminate.side_effect = RuntimeError("terminate failed")

    # should not raise — terminate errors are swallowed
    result = ModalSandbox(_config()).run(_spec(create_pr=False))

    assert result.success is True


@patch("sandbox.sandbox.modal.Sandbox.create", side_effect=RuntimeError("Modal API error"))
def test_sandbox_create_failure_returns_failed_result(_mock_create):
    result = ModalSandbox(_config()).run(_spec())

    assert result.success is False
    assert "Modal API error" in result.error


# ------------------------------------------------------------------ env vars


@patch("sandbox.sandbox.modal.Sandbox.create")
def test_spec_env_passed_as_modal_secret(mock_create):
    sb = MagicMock()
    mock_create.return_value = sb
    sb.exec.return_value = _make_proc()

    with patch("sandbox.sandbox.modal.Secret.from_dict") as mock_secret:
        mock_secret.return_value = MagicMock()
        ModalSandbox(_config()).run(_spec(env={"FOO": "bar"}))

    called_env = mock_secret.call_args[0][0]
    assert called_env["FOO"] == "bar"
    assert "OPENCODE_TIMEOUT" in called_env


@patch("sandbox.sandbox.modal.Sandbox.create")
def test_config_env_merged_with_spec_env(mock_create):
    """container_env + env_for_backend + spec.env are all merged; spec takes precedence."""
    sb = MagicMock()
    mock_create.return_value = sb
    sb.exec.return_value = _make_proc()

    config = SandboxConfig(
        openai_base_url="https://serve.modal.run/v1",
        openai_api_key="modal",
        github_token="ghp_abc",
    )

    with patch("sandbox.sandbox.modal.Secret.from_dict") as mock_secret:
        mock_secret.return_value = MagicMock()
        # env_for_backend("opencode") normalises base URL and includes OPENAI_* vars
        ModalSandbox(config).run(_spec(env={"CUSTOM_VAR": "custom"}))

    merged = mock_secret.call_args[0][0]
    # OPENAI_BASE_URL comes from env_for_backend, not container_env
    assert merged["OPENAI_BASE_URL"] == "https://serve.modal.run/v1"
    # GITHUB_TOKEN comes from container_env
    assert merged["GITHUB_TOKEN"] == "ghp_abc"
    assert merged["CUSTOM_VAR"] == "custom"


@patch("sandbox.sandbox.modal.Sandbox.create")
def test_empty_env_passes_no_secrets(mock_create):
    sb = MagicMock()
    mock_create.return_value = sb
    sb.exec.return_value = _make_proc()

    ModalSandbox(_config()).run(_spec(env={}))

    _, kwargs = mock_create.call_args
    # OPENCODE_TIMEOUT is always injected so secrets is never empty
    assert kwargs["secrets"] != []


# branch_name and agent command tests live in test_git_ops.py and test_backends.py
