from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(Exception):
    """Raised when required tooling is missing or Docker is unreachable."""


_DEFAULT_IMAGE = "mcr.microsoft.com/devcontainers/base:ubuntu-24.04"


@dataclass
class SandboxConfig:
    docker_host: str = ""  # empty → use default local socket
    default_image: str = _DEFAULT_IMAGE
    workspace_timeout_seconds: int = 300

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> SandboxConfig:
        """Load config from environment variables, optionally reading a .env file first."""
        load_dotenv(env_file or Path(".env"), override=False)

        return cls(
            docker_host=os.getenv("DOCKER_HOST", "").strip(),
            default_image=os.getenv("AGENT_DEFAULT_IMAGE", _DEFAULT_IMAGE).strip(),
            workspace_timeout_seconds=int(os.getenv("AGENT_WORKSPACE_TIMEOUT", "300").strip()),
        )

    def validate_connection(self) -> None:
        """Check devcontainer CLI is installed and Docker daemon is running."""
        self._require_devcontainer_cli()
        self._require_docker()

    def _require_devcontainer_cli(self) -> None:
        if shutil.which("devcontainer"):
            return
        # Fall back to npx — installs on first use, acceptable for CI
        result = subprocess.run(
            ["npx", "--yes", "@devcontainers/cli", "--version"],
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise ConfigError(
                "devcontainer CLI not found.\n"
                "Install it with: npm install -g @devcontainers/cli\n"
                "Or ensure Node.js / npx is on PATH."
            )

    def _require_docker(self) -> None:
        env = {**os.environ}
        if self.docker_host:
            env["DOCKER_HOST"] = self.docker_host
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            env=env,
            timeout=10,
        )
        if result.returncode != 0:
            raise ConfigError(
                "Docker daemon is not running or not reachable.\n"
                "Start Docker Desktop or 'sudo systemctl start docker'."
            )
