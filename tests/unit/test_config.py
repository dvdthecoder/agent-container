"""Unit tests for SandboxConfig — no external services required."""

import pytest

from sandbox.config import ConfigError, SandboxConfig


class TestSandboxConfigFromEnv:
    def test_raises_when_both_vars_missing(self, monkeypatch):
        monkeypatch.delenv("DAYTONA_SERVER_URL", raising=False)
        monkeypatch.delenv("DAYTONA_API_KEY", raising=False)

        with pytest.raises(ConfigError) as exc:
            SandboxConfig.from_env()

        assert "DAYTONA_SERVER_URL" in str(exc.value)
        assert "DAYTONA_API_KEY" in str(exc.value)

    def test_raises_when_server_url_missing(self, monkeypatch):
        monkeypatch.delenv("DAYTONA_SERVER_URL", raising=False)
        monkeypatch.setenv("DAYTONA_API_KEY", "key-123")

        with pytest.raises(ConfigError) as exc:
            SandboxConfig.from_env()

        assert "DAYTONA_SERVER_URL" in str(exc.value)
        assert "DAYTONA_API_KEY" not in str(exc.value)

    def test_raises_when_api_key_missing(self, monkeypatch):
        monkeypatch.setenv("DAYTONA_SERVER_URL", "http://localhost:3986")
        monkeypatch.delenv("DAYTONA_API_KEY", raising=False)

        with pytest.raises(ConfigError) as exc:
            SandboxConfig.from_env()

        assert "DAYTONA_API_KEY" in str(exc.value)

    def test_error_message_mentions_env_example(self, monkeypatch):
        monkeypatch.delenv("DAYTONA_SERVER_URL", raising=False)
        monkeypatch.delenv("DAYTONA_API_KEY", raising=False)

        with pytest.raises(ConfigError) as exc:
            SandboxConfig.from_env()

        assert ".env.example" in str(exc.value)

    def test_loads_correctly_with_valid_env(self, monkeypatch):
        monkeypatch.setenv("DAYTONA_SERVER_URL", "http://localhost:3986")
        monkeypatch.setenv("DAYTONA_API_KEY", "key-abc")

        config = SandboxConfig.from_env()

        assert config.server_url == "http://localhost:3986"
        assert config.api_key == "key-abc"

    def test_default_values(self, monkeypatch):
        monkeypatch.setenv("DAYTONA_SERVER_URL", "http://localhost:3986")
        monkeypatch.setenv("DAYTONA_API_KEY", "key-abc")
        monkeypatch.delenv("DAYTONA_TARGET", raising=False)
        monkeypatch.delenv("DAYTONA_DEFAULT_IMAGE", raising=False)
        monkeypatch.delenv("DAYTONA_WORKSPACE_TIMEOUT", raising=False)

        config = SandboxConfig.from_env()

        assert config.target == "local"
        assert config.default_image == "ubuntu:22.04"
        assert config.workspace_timeout_seconds == 300

    def test_overrides_defaults_from_env(self, monkeypatch):
        monkeypatch.setenv("DAYTONA_SERVER_URL", "http://daytona.internal:3986")
        monkeypatch.setenv("DAYTONA_API_KEY", "key-xyz")
        monkeypatch.setenv("DAYTONA_TARGET", "remote")
        monkeypatch.setenv("DAYTONA_DEFAULT_IMAGE", "python:3.11-slim")
        monkeypatch.setenv("DAYTONA_WORKSPACE_TIMEOUT", "600")

        config = SandboxConfig.from_env()

        assert config.target == "remote"
        assert config.default_image == "python:3.11-slim"
        assert config.workspace_timeout_seconds == 600

    def test_strips_whitespace_from_values(self, monkeypatch):
        monkeypatch.setenv("DAYTONA_SERVER_URL", "  http://localhost:3986  ")
        monkeypatch.setenv("DAYTONA_API_KEY", "  key-abc  ")

        config = SandboxConfig.from_env()

        assert config.server_url == "http://localhost:3986"
        assert config.api_key == "key-abc"
