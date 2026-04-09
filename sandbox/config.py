from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import httpx
from dotenv import load_dotenv


class ConfigError(Exception):
    """Raised when required configuration is missing or the Daytona server is unreachable."""


@dataclass
class SandboxConfig:
    server_url: str
    api_key: str
    target: str = "local"
    default_image: str = "ubuntu:22.04"
    workspace_timeout_seconds: int = 300

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> SandboxConfig:
        """Load config from environment variables, optionally reading a .env file first."""
        load_dotenv(env_file or Path(".env"), override=False)

        server_url = os.getenv("DAYTONA_SERVER_URL", "").strip()
        api_key = os.getenv("DAYTONA_API_KEY", "").strip()

        missing = [
            name
            for name, value in [
                ("DAYTONA_SERVER_URL", server_url),
                ("DAYTONA_API_KEY", api_key),
            ]
            if not value
        ]
        if missing:
            raise ConfigError(
                f"Missing required environment variable(s): {', '.join(missing)}\n"
                f"Copy .env.example to .env and fill in the values."
            )

        return cls(
            server_url=server_url,
            api_key=api_key,
            target=os.getenv("DAYTONA_TARGET", "local"),
            default_image=os.getenv("DAYTONA_DEFAULT_IMAGE", "ubuntu:22.04"),
            workspace_timeout_seconds=int(os.getenv("DAYTONA_WORKSPACE_TIMEOUT", "300")),
        )

    def validate_connection(self) -> None:
        """Ping the Daytona server health endpoint. Raises ConfigError if unreachable."""
        try:
            response = httpx.get(f"{self.server_url}/health", timeout=5.0)
            response.raise_for_status()
        except httpx.ConnectError as exc:
            raise ConfigError(
                f"Cannot connect to Daytona at {self.server_url}\nIs 'daytona serve' running?"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ConfigError(
                f"Daytona health check returned {exc.response.status_code} at {self.server_url}"
            ) from exc
        except Exception as exc:
            raise ConfigError(f"Daytona health check failed: {exc}") from exc
