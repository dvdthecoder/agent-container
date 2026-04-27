"""Unit tests for SandboxConfig — no external services required."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from sandbox.config import _DEFAULT_IMAGE, ConfigError, SandboxConfig  # noqa: PLC2701


class TestSandboxConfigFromEnv:
    def test_default_values(self, monkeypatch):
        for var in [
            "AGENT_DEFAULT_IMAGE",
            "AGENT_WORKSPACE_TIMEOUT",
            "OPENAI_BASE_URL",
            "OPENAI_API_KEY",
            "OPENCODE_MODEL",
            "GITHUB_TOKEN",
            "GITLAB_TOKEN",
        ]:
            monkeypatch.delenv(var, raising=False)

        with patch("sandbox.config.load_dotenv"):
            config = SandboxConfig.from_env()

        assert config.default_image == _DEFAULT_IMAGE
        assert config.workspace_timeout_seconds == 300
        assert config.openai_base_url == ""
        assert config.openai_api_key == ""
        assert config.opencode_model == ""
        assert config.github_token == ""
        assert config.gitlab_token == ""

    def test_overrides_defaults_from_env(self, monkeypatch):
        monkeypatch.setenv("AGENT_DEFAULT_IMAGE", "python:3.11-slim")
        monkeypatch.setenv("AGENT_WORKSPACE_TIMEOUT", "600")

        config = SandboxConfig.from_env()

        assert config.default_image == "python:3.11-slim"
        assert config.workspace_timeout_seconds == 600

    def test_loads_model_and_git_vars(self, monkeypatch):
        monkeypatch.setenv("OPENAI_BASE_URL", "https://org--serve.modal.run/v1")
        monkeypatch.setenv("OPENAI_API_KEY", "modal")
        monkeypatch.setenv("OPENCODE_MODEL", "qwen3-coder")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_abc")
        monkeypatch.setenv("GITLAB_TOKEN", "glpat_xyz")

        config = SandboxConfig.from_env()

        assert config.openai_base_url == "https://org--serve.modal.run/v1"
        assert config.openai_api_key == "modal"
        assert config.opencode_model == "qwen3-coder"
        assert config.github_token == "ghp_abc"
        assert config.gitlab_token == "glpat_xyz"

    def test_strips_whitespace_from_values(self, monkeypatch):
        monkeypatch.setenv("AGENT_DEFAULT_IMAGE", "  python:3.11-slim  ")
        monkeypatch.setenv("AGENT_WORKSPACE_TIMEOUT", "  120  ")
        monkeypatch.setenv("OPENAI_BASE_URL", "  https://example.com  ")

        config = SandboxConfig.from_env()

        assert config.default_image == "python:3.11-slim"
        assert config.workspace_timeout_seconds == 120
        assert config.openai_base_url == "https://example.com"


class TestSandboxConfigContainerEnv:
    def test_empty_when_no_vars_set(self):
        config = SandboxConfig()
        assert config.container_env() == {}

    def test_includes_set_vars_only(self):
        config = SandboxConfig(
            openai_base_url="https://example.com/v1",
            openai_api_key="modal",
            github_token="ghp_abc",
        )
        env = config.container_env()

        assert env == {
            "OPENAI_BASE_URL": "https://example.com/v1",
            "OPENAI_API_KEY": "modal",
            "GITHUB_TOKEN": "ghp_abc",
        }
        assert "GITLAB_TOKEN" not in env
        assert "OPENCODE_MODEL" not in env

    def test_excludes_empty_strings(self):
        config = SandboxConfig(openai_base_url="", github_token="ghp_abc")
        env = config.container_env()

        assert "OPENAI_BASE_URL" not in env
        assert env["GITHUB_TOKEN"] == "ghp_abc"


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
