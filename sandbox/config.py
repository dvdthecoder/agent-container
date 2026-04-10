from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(Exception):
    """Raised when required configuration is missing or Modal is unreachable."""


_DEFAULT_IMAGE = "mcr.microsoft.com/devcontainers/base:ubuntu-24.04"


@dataclass
class SandboxConfig:
    # Container defaults
    default_image: str = _DEFAULT_IMAGE
    workspace_timeout_seconds: int = 300

    # Model endpoint — forwarded into every sandbox container
    openai_base_url: str = ""
    openai_api_key: str = ""
    opencode_model: str = ""

    # Git tokens — forwarded into every sandbox container
    github_token: str = ""
    gitlab_token: str = ""

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> SandboxConfig:
        """Load config from environment variables, optionally reading a .env file first."""
        load_dotenv(env_file or Path(".env"), override=False)

        return cls(
            default_image=os.getenv("AGENT_DEFAULT_IMAGE", _DEFAULT_IMAGE).strip(),
            workspace_timeout_seconds=int(os.getenv("AGENT_WORKSPACE_TIMEOUT", "300").strip()),
            openai_base_url=os.getenv("OPENAI_BASE_URL", "").strip(),
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            opencode_model=os.getenv("OPENCODE_MODEL", "").strip(),
            github_token=os.getenv("GITHUB_TOKEN", "").strip(),
            gitlab_token=os.getenv("GITLAB_TOKEN", "").strip(),
        )

    def container_env(self) -> dict[str, str]:
        """Environment variables to inject into every sandbox container."""
        env: dict[str, str] = {}
        for key, value in [
            ("OPENAI_BASE_URL", self.openai_base_url),
            ("OPENAI_API_KEY", self.openai_api_key),
            ("OPENCODE_MODEL", self.opencode_model),
            ("GITHUB_TOKEN", self.github_token),
            ("GITLAB_TOKEN", self.gitlab_token),
        ]:
            if value:
                env[key] = value
        return env

    def validate_connection(self) -> None:
        """Check Modal CLI is installed and the current token is valid."""
        self._require_modal_cli()
        self._require_modal_auth()

    def _require_modal_cli(self) -> None:
        result = subprocess.run(
            ["modal", "--version"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise ConfigError("modal CLI not found.\nInstall it with: pip install modal")

    def _require_modal_auth(self) -> None:
        result = subprocess.run(
            ["modal", "token", "current"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise ConfigError(
                "Modal token not configured.\n"
                "Run: modal token new\n"
                "Or set MODAL_TOKEN_ID and MODAL_TOKEN_SECRET in .env"
            )
