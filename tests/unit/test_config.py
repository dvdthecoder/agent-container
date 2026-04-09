"""Unit tests for SandboxConfig — no external services required."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from sandbox.config import _DEFAULT_IMAGE, ConfigError, SandboxConfig  # noqa: PLC2701


class TestSandboxConfigFromEnv:
    def test_default_values(self, monkeypatch):
        monkeypatch.delenv("DOCKER_HOST", raising=False)
        monkeypatch.delenv("AGENT_DEFAULT_IMAGE", raising=False)
        monkeypatch.delenv("AGENT_WORKSPACE_TIMEOUT", raising=False)

        config = SandboxConfig.from_env()

        assert config.docker_host == ""
        assert config.default_image == _DEFAULT_IMAGE
        assert config.workspace_timeout_seconds == 300

    def test_overrides_defaults_from_env(self, monkeypatch):
        monkeypatch.setenv("DOCKER_HOST", "tcp://remote:2376")
        monkeypatch.setenv("AGENT_DEFAULT_IMAGE", "python:3.11-slim")
        monkeypatch.setenv("AGENT_WORKSPACE_TIMEOUT", "600")

        config = SandboxConfig.from_env()

        assert config.docker_host == "tcp://remote:2376"
        assert config.default_image == "python:3.11-slim"
        assert config.workspace_timeout_seconds == 600

    def test_strips_whitespace_from_values(self, monkeypatch):
        monkeypatch.setenv("AGENT_DEFAULT_IMAGE", "  python:3.11-slim  ")
        monkeypatch.setenv("AGENT_WORKSPACE_TIMEOUT", "  120  ")

        config = SandboxConfig.from_env()

        assert config.default_image == "python:3.11-slim"
        assert config.workspace_timeout_seconds == 120

    def test_docker_host_empty_by_default(self, monkeypatch):
        monkeypatch.delenv("DOCKER_HOST", raising=False)

        config = SandboxConfig.from_env()

        assert config.docker_host == ""


class TestSandboxConfigValidateConnection:
    def _config(self) -> SandboxConfig:
        return SandboxConfig()

    @patch("sandbox.config.shutil.which", return_value="/usr/local/bin/devcontainer")
    @patch("sandbox.config.subprocess.run")
    def test_passes_when_cli_and_docker_available(self, mock_run, _mock_which):
        mock_run.return_value = MagicMock(returncode=0)

        # should not raise
        self._config().validate_connection()

        # only docker info should be called (which() found devcontainer)
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0] == ["docker", "info"]

    @patch("sandbox.config.shutil.which", return_value=None)
    @patch("sandbox.config.subprocess.run")
    def test_falls_back_to_npx_when_cli_not_on_path(self, mock_run, _mock_which):
        # npx succeeds, docker info succeeds
        mock_run.return_value = MagicMock(returncode=0)

        self._config().validate_connection()

        calls = [c[0][0] for c in mock_run.call_args_list]
        assert any("npx" in cmd for cmd in calls)

    @patch("sandbox.config.shutil.which", return_value=None)
    @patch("sandbox.config.subprocess.run")
    def test_raises_config_error_when_npx_fails(self, mock_run, _mock_which):
        mock_run.return_value = MagicMock(returncode=1)

        with pytest.raises(ConfigError, match="devcontainer CLI not found"):
            self._config().validate_connection()

    @patch("sandbox.config.shutil.which", return_value="/usr/local/bin/devcontainer")
    @patch("sandbox.config.subprocess.run")
    def test_raises_config_error_when_docker_not_running(self, mock_run, _mock_which):
        mock_run.return_value = MagicMock(returncode=1)

        with pytest.raises(ConfigError, match="Docker daemon"):
            self._config().validate_connection()

    @patch("sandbox.config.shutil.which", return_value="/usr/local/bin/devcontainer")
    @patch("sandbox.config.subprocess.run")
    def test_passes_docker_host_env_when_set(self, mock_run, _mock_which):
        mock_run.return_value = MagicMock(returncode=0)
        config = SandboxConfig(docker_host="tcp://remote:2376")

        config.validate_connection()

        docker_call = mock_run.call_args
        assert docker_call[1]["env"]["DOCKER_HOST"] == "tcp://remote:2376"

    @patch("sandbox.config.shutil.which", return_value="/usr/local/bin/devcontainer")
    @patch(
        "sandbox.config.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=10),
    )
    def test_timeout_on_docker_info_raises_config_error(self, _mock_run, _mock_which):
        with pytest.raises(subprocess.TimeoutExpired):
            self._config().validate_connection()
