"""Unit tests for agent-run CLI — ModalSandbox fully mocked."""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from agent.cli import cli
from sandbox.result import AgentTaskResult


def _ok(**kwargs) -> AgentTaskResult:
    defaults = dict(
        success=True,
        run_id="sb-test",
        diff="diff --git a/f b/f\n+fix",
        diff_stat="1 file changed",
        duration_seconds=12.3,
        backend="opencode",
    )
    return AgentTaskResult(**{**defaults, **kwargs})


def _fail(**kwargs) -> AgentTaskResult:
    return _ok(success=False, error="something broke", **kwargs)


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def mock_sandbox():
    with patch("agent.cli.ModalSandbox") as mock_cls:
        instance = MagicMock()
        mock_cls.return_value = instance
        instance.run.return_value = _ok()
        yield instance


@pytest.fixture()
def mock_config():
    with patch("agent.cli.SandboxConfig") as mock_cls:
        mock_cls.from_env.return_value = MagicMock()
        yield mock_cls


# ------------------------------------------------------------------ happy path


def test_run_exits_zero_on_success(runner, mock_sandbox, mock_config):
    result = runner.invoke(
        cli, ["run", "--repo", "https://github.com/org/repo", "--task", "fix it"]
    )
    assert result.exit_code == 0


def test_run_prints_json_to_stdout(runner, mock_sandbox, mock_config):
    result = runner.invoke(
        cli, ["run", "--repo", "https://github.com/org/repo", "--task", "fix it"]
    )
    assert '"success"' in result.output
    assert '"run_id"' in result.output


def test_run_passes_repo_and_task_to_spec(runner, mock_sandbox, mock_config):
    runner.invoke(cli, ["run", "--repo", "https://github.com/org/repo", "--task", "fix the bug"])

    spec = mock_sandbox.run.call_args[0][0]
    assert spec.repo == "https://github.com/org/repo"
    assert spec.task == "fix the bug"


def test_run_defaults(runner, mock_sandbox, mock_config):
    runner.invoke(cli, ["run", "--repo", "https://github.com/org/repo", "--task", "fix it"])

    spec = mock_sandbox.run.call_args[0][0]
    assert spec.base_branch == "main"
    assert spec.backend == "opencode"
    assert spec.timeout_seconds == 300
    assert spec.create_pr is True


def test_run_accepts_all_flags(runner, mock_sandbox, mock_config):
    runner.invoke(
        cli,
        [
            "run",
            "--repo",
            "https://github.com/org/repo",
            "--task",
            "fix it",
            "--backend",
            "claude",
            "--branch",
            "develop",
            "--timeout",
            "120",
            "--no-pr",
            "--image",
            "python:3.11-slim",
        ],
    )

    spec = mock_sandbox.run.call_args[0][0]
    assert spec.backend == "claude"
    assert spec.base_branch == "develop"
    assert spec.timeout_seconds == 120
    assert spec.create_pr is False
    assert spec.image == "python:3.11-slim"


def test_run_accepts_task_file(runner, mock_sandbox, mock_config, tmp_path):
    task_file = tmp_path / "task.md"
    task_file.write_text("fix the login bug")

    result = runner.invoke(
        cli,
        [
            "run",
            "--repo",
            "https://github.com/org/repo",
            "--task-file",
            str(task_file),
        ],
    )

    assert result.exit_code == 0
    spec = mock_sandbox.run.call_args[0][0]
    assert spec.task_file == task_file


# ------------------------------------------------------------------ failures


def test_run_exits_one_on_failure(runner, mock_config):
    with patch("agent.cli.ModalSandbox") as mock_cls:
        mock_cls.return_value.run.return_value = _fail()
        result = runner.invoke(
            cli, ["run", "--repo", "https://github.com/org/repo", "--task", "fix it"]
        )

    assert result.exit_code == 1


def test_run_requires_task_or_task_file(runner, mock_config):
    result = runner.invoke(cli, ["run", "--repo", "https://github.com/org/repo"])
    assert result.exit_code != 0
    assert "task" in result.output.lower()


def test_run_rejects_both_task_and_task_file(runner, mock_config, tmp_path):
    task_file = tmp_path / "task.md"
    task_file.write_text("fix it")

    result = runner.invoke(
        cli,
        [
            "run",
            "--repo",
            "https://github.com/org/repo",
            "--task",
            "fix it",
            "--task-file",
            str(task_file),
        ],
    )
    assert result.exit_code != 0
    assert "not both" in result.output.lower() or "task" in result.output.lower()


def test_run_exits_one_on_config_error(runner):
    with patch("agent.cli.SandboxConfig") as mock_cls:
        from sandbox.config import ConfigError

        mock_cls.from_env.side_effect = ConfigError("Modal token not configured")

        result = runner.invoke(
            cli, ["run", "--repo", "https://github.com/org/repo", "--task", "fix it"]
        )

    assert result.exit_code == 1
    assert "Configuration error" in result.output


# ------------------------------------------------------------------ output


def test_success_emits_duration_to_stderr(runner, mock_sandbox, mock_config):
    result = runner.invoke(
        cli, ["run", "--repo", "https://github.com/org/repo", "--task", "fix it"]
    )
    # stderr is mixed into output in CliRunner by default
    assert "12.3s" in result.output


def test_failure_emits_error_to_stderr(runner, mock_config):
    with patch("agent.cli.ModalSandbox") as mock_cls:
        mock_cls.return_value.run.return_value = _fail()
        result = runner.invoke(
            cli, ["run", "--repo", "https://github.com/org/repo", "--task", "fix it"]
        )

    assert "something broke" in result.output


def test_invoking_cli_without_subcommand_shows_help(runner):
    result = runner.invoke(cli, [])
    assert result.exit_code == 0
    assert "Usage" in result.output
