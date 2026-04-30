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

    def token_for(self, provider_name: str) -> str:
        """Return the stored access token for *provider_name*, or '' if not configured."""
        if provider_name == "github":
            return self.github_token
        if provider_name == "gitlab":
            return self.gitlab_token
        return ""

    def container_env(self) -> dict[str, str]:
        """Shared env vars injected into every sandbox container (git tokens only).

        Inference-specific vars are emitted by :meth:`env_for_backend` so each
        backend receives the vars it needs in the format it expects.
        """
        env: dict[str, str] = {}
        for key, value in [
            ("GITHUB_TOKEN", self.github_token),
            ("GITLAB_TOKEN", self.gitlab_token),
        ]:
            if value:
                env[key] = value
        return env

    def env_for_backend(self, backend: str) -> dict[str, str]:
        """Inference env vars formatted for *backend*.

        Each backend has different expectations for URL format, key names, and
        which vars are required — this is the single place those differences live.

        The caller merges: ``container_env() | env_for_backend(backend) | spec.env``
        so spec-level overrides always win.
        """
        if backend in ("aider", "opencode"):
            # Both backends use the OpenAI-compatible API.
            # OPENAI_BASE_URL must include the /v1 suffix — the OpenAI SDK
            # appends /chat/completions (or /responses) to whatever base URL it
            # receives, so omitting /v1 sends requests to the wrong path.
            raw = self.openai_base_url.rstrip("/")
            base_url = raw if raw.endswith("/v1") else f"{raw}/v1" if raw else ""
            env: dict[str, str] = {}
            if base_url:
                env["OPENAI_BASE_URL"] = base_url
            if self.openai_api_key:
                env["OPENAI_API_KEY"] = self.openai_api_key
            if self.opencode_model:
                env["OPENCODE_MODEL"] = self.opencode_model
            return env

        # claude and gemini use their own API keys injected via spec.env —
        # they don't need the self-hosted inference vars at all.
        return {}

    def validate_connection(self) -> None:
        """Check Modal CLI is installed and the current token is valid."""
        self._require_modal_cli()
        self._require_modal_auth()

    def _require_modal_cli(self) -> None:
        result = subprocess.run(  # noqa: S603
            ["modal", "--version"],  # noqa: S607 — modal CLI installed in dev environment
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise ConfigError("modal CLI not found.\nInstall it with: pip install modal")

    def _require_modal_auth(self) -> None:
        result = subprocess.run(  # noqa: S603
            ["modal", "token", "current"],  # noqa: S607 — modal CLI installed in dev environment
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise ConfigError(
                "Modal token not configured.\n"
                "Run: modal token new\n"
                "Or set MODAL_TOKEN_ID and MODAL_TOKEN_SECRET in .env"
            )
