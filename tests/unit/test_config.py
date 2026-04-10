"""Unit tests for SandboxConfig — no external services required."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from sandbox.config import _DEFAULT_IMAGE, ConfigError, SandboxConfig  # noqa: PLC2701


class TestSandboxConfigFromEnv:
    def test_default_values(self, monkeypatch):
        monkeypatch.delenv("AGENT_DEFAULT_IMAGE", raising=False)
        monkeypatch.delenv("AGENT_WORKSPACE_TIMEOUT", raising=False)

        config = SandboxConfig.from_env()

        assert config.default_image == _DEFAULT_IMAGE
        assert config.workspace_timeout_seconds == 300

    def test_overrides_defaults_from_env(self, monkeypatch):
        monkeypatch.setenv("AGENT_DEFAULT_IMAGE", "python:3.11-slim")
        monkeypatch.setenv("AGENT_WORKSPACE_TIMEOUT", "600")

        config = SandboxConfig.from_env()

        assert config.default_image == "python:3.11-slim"
        assert config.workspace_timeout_seconds == 600

    def test_strips_whitespace_from_values(self, monkeypatch):
        monkeypatch.setenv("AGENT_DEFAULT_IMAGE", "  python:3.11-slim  ")
        monkeypatch.setenv("AGENT_WORKSPACE_TIMEOUT", "  120  ")

        config = SandboxConfig.from_env()

        assert config.default_image == "python:3.11-slim"
        assert config.workspace_timeout_seconds == 120


class TestSandboxConfigValidateConnection:
    def _config(self) -> SandboxConfig:
        return SandboxConfig()

    @patch("sandbox.config.subprocess.run")
    def test_passes_when_modal_cli_and_auth_available(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        self._config().validate_connection()

        assert mock_run.call_count == 2
        calls = [c[0][0] for c in mock_run.call_args_list]
        assert calls[0] == ["modal", "--version"]
        assert calls[1] == ["modal", "token", "current"]

    @patch("sandbox.config.subprocess.run")
    def test_raises_when_modal_cli_missing(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)

        with pytest.raises(ConfigError, match="modal CLI not found"):
            self._config().validate_connection()

    @patch("sandbox.config.subprocess.run")
    def test_raises_when_modal_token_not_configured(self, mock_run):
        # First call (--version) succeeds, second (token current) fails
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=1),
        ]

        with pytest.raises(ConfigError, match="Modal token not configured"):
            self._config().validate_connection()

    @patch(
        "sandbox.config.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="modal", timeout=10),
    )
    def test_timeout_raises(self, _mock_run):
        with pytest.raises(subprocess.TimeoutExpired):
            self._config().validate_connection()
