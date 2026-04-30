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
    """container_env() is git tokens only — inference vars live in env_for_backend()."""

    def test_empty_when_no_vars_set(self):
        config = SandboxConfig()
        assert config.container_env() == {}

    def test_includes_git_tokens_only(self):
        config = SandboxConfig(
            openai_base_url="https://example.com/v1",
            openai_api_key="modal",
            github_token="ghp_abc",
            gitlab_token="glpat_xyz",
        )
        env = config.container_env()

        assert env == {"GITHUB_TOKEN": "ghp_abc", "GITLAB_TOKEN": "glpat_xyz"}
        assert "OPENAI_BASE_URL" not in env
        assert "OPENAI_API_KEY" not in env
        assert "OPENCODE_MODEL" not in env

    def test_excludes_empty_tokens(self):
        config = SandboxConfig(github_token="ghp_abc", gitlab_token="")
        env = config.container_env()

        assert env == {"GITHUB_TOKEN": "ghp_abc"}
        assert "GITLAB_TOKEN" not in env


class TestSandboxConfigEnvForBackend:
    """env_for_backend() emits inference vars normalised for each backend."""

    def test_aider_includes_base_url_with_v1(self):
        config = SandboxConfig(openai_base_url="https://host", openai_api_key="modal")
        env = config.env_for_backend("aider")

        assert env["OPENAI_BASE_URL"] == "https://host/v1"
        assert env["OPENAI_API_KEY"] == "modal"

    def test_aider_preserves_v1_when_already_present(self):
        config = SandboxConfig(openai_base_url="https://host/v1")
        env = config.env_for_backend("aider")

        assert env["OPENAI_BASE_URL"] == "https://host/v1"

    def test_aider_strips_trailing_slash_before_adding_v1(self):
        config = SandboxConfig(openai_base_url="https://host/")
        env = config.env_for_backend("aider")

        assert env["OPENAI_BASE_URL"] == "https://host/v1"

    def test_aider_omits_base_url_when_empty(self):
        config = SandboxConfig(openai_base_url="")
        env = config.env_for_backend("aider")

        assert "OPENAI_BASE_URL" not in env

    def test_aider_includes_model_when_set(self):
        config = SandboxConfig(opencode_model="qwen2.5-coder")
        env = config.env_for_backend("aider")

        assert env["OPENCODE_MODEL"] == "qwen2.5-coder"

    def test_opencode_normalises_base_url_same_as_aider(self):
        config = SandboxConfig(openai_base_url="https://host", openai_api_key="modal")
        assert config.env_for_backend("opencode") == config.env_for_backend("aider")

    def test_claude_returns_empty(self):
        config = SandboxConfig(openai_base_url="https://host", openai_api_key="modal")
        assert config.env_for_backend("claude") == {}

    def test_gemini_returns_empty(self):
        config = SandboxConfig(openai_base_url="https://host", openai_api_key="modal")
        assert config.env_for_backend("gemini") == {}

    def test_unknown_backend_returns_empty(self):
        config = SandboxConfig(openai_base_url="https://host")
        assert config.env_for_backend("unknown-future-backend") == {}


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
