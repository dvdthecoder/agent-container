"""Unit tests for ModalSandbox — Modal SDK fully mocked, no network required."""

from unittest.mock import MagicMock, patch

import pytest

from sandbox.config import SandboxConfig
from sandbox.sandbox import ModalSandbox, _agent_command
from sandbox.spec import AgentTaskSpec


def _spec(**kwargs) -> AgentTaskSpec:
    defaults = dict(repo="https://github.com/org/repo", task="fix it")
    return AgentTaskSpec(**{**defaults, **kwargs})


def _config() -> SandboxConfig:
    return SandboxConfig()


def _make_proc(stdout: str = "", returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.stdout.read.return_value = stdout
    proc.stderr.read.return_value = ""
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

    sb.exec.side_effect = [clone_proc, agent_proc, diff_proc, stat_proc]

    result = ModalSandbox(_config()).run(_spec())

    assert result.success is True
    assert result.run_id == "sb-abc123"
    assert result.diff == "diff --git a/f b/f\n+fix"
    assert result.diff_stat == "1 file changed"
    assert result.error is None
    assert result.backend == "opencode"
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

    sb.exec.side_effect = [clone_proc, agent_proc, diff_proc, stat_proc]

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
    sb.exec.return_value = _make_proc()
    sb.terminate.side_effect = RuntimeError("terminate failed")

    # should not raise — terminate errors are swallowed
    result = ModalSandbox(_config()).run(_spec())

    assert result.success is True


@patch("sandbox.sandbox.modal.Sandbox.create", side_effect=RuntimeError("Modal API error"))
def test_sandbox_create_failure_returns_failed_result(_mock_create):
    result = ModalSandbox(_config()).run(_spec())

    assert result.success is False
    assert "Modal API error" in result.error


# ------------------------------------------------------------------ agent commands


@pytest.mark.parametrize(
    "backend, expected_start",
    [
        ("opencode", ["opencode", "--print", "-m"]),
        ("claude", ["claude", "--print"]),
        ("gemini", ["gemini", "--yolo", "-p"]),
        ("stub", ["sh", "-c"]),
    ],
)
def test_agent_command_dispatch(backend, expected_start):
    cmd = _agent_command(backend, "fix it")
    assert cmd[: len(expected_start)] == expected_start


def test_agent_command_unknown_backend_raises():
    with pytest.raises(ValueError, match="Unknown backend"):
        _agent_command("gpt-pilot", "fix it")


def test_agent_command_stub_quotes_task():
    cmd = _agent_command("stub", "task with 'quotes'")
    # sh -c arg must safely contain the task
    assert "task with" in cmd[-1]


# ------------------------------------------------------------------ env vars


@patch("sandbox.sandbox.modal.Sandbox.create")
def test_spec_env_passed_as_modal_secret(mock_create):
    sb = MagicMock()
    mock_create.return_value = sb
    sb.exec.return_value = _make_proc()

    with patch("sandbox.sandbox.modal.Secret.from_dict") as mock_secret:
        mock_secret.return_value = MagicMock()
        ModalSandbox(_config()).run(_spec(env={"FOO": "bar"}))

    mock_secret.assert_called_once_with({"FOO": "bar"})


@patch("sandbox.sandbox.modal.Sandbox.create")
def test_config_env_merged_with_spec_env(mock_create):
    """config.container_env() is merged with spec.env; spec takes precedence."""
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
        ModalSandbox(config).run(_spec(env={"CUSTOM_VAR": "custom"}))

    merged = mock_secret.call_args[0][0]
    assert merged["OPENAI_BASE_URL"] == "https://serve.modal.run/v1"
    assert merged["GITHUB_TOKEN"] == "ghp_abc"
    assert merged["CUSTOM_VAR"] == "custom"


@patch("sandbox.sandbox.modal.Sandbox.create")
def test_empty_env_passes_no_secrets(mock_create):
    sb = MagicMock()
    mock_create.return_value = sb
    sb.exec.return_value = _make_proc()

    ModalSandbox(_config()).run(_spec(env={}))

    _, kwargs = mock_create.call_args
    assert kwargs["secrets"] == []
