from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentTaskSpec:
    """Input contract for a single agent sandbox run."""

    repo: str
    task: str | None = None
    task_file: Path | None = None
    base_branch: str = "main"
    image: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 300
    create_pr: bool = True
    run_tests: bool = True  # auto-detect and run the project test suite
    backend: str = "opencode"  # opencode | claude | gemini

    def __post_init__(self) -> None:
        if self.task is None and self.task_file is None:
            raise ValueError("Provide either 'task' or 'task_file' — both are None.")

        if self.task is not None and self.task_file is not None:
            raise ValueError("Provide either 'task' or 'task_file', not both.")

        if self.task_file is not None:
            self.task_file = Path(self.task_file)
            if not self.task_file.exists():
                raise ValueError(f"task_file not found: {self.task_file}")

        if not self.repo.startswith(("https://", "git@")):
            raise ValueError(f"'repo' must be a full URL (https:// or git@...), got: {self.repo!r}")

        if self.timeout_seconds < 1:
            raise ValueError(f"timeout_seconds must be >= 1, got {self.timeout_seconds}")

    def resolved_task(self) -> str:
        """Return the task string, reading from file if task_file was provided."""
        if self.task is not None:
            return self.task
        return self.task_file.read_text(encoding="utf-8").strip()  # type: ignore[union-attr]

    def resolved_image(self, default_image: str) -> str:
        """Return the Docker image to use, falling back to SandboxConfig.default_image."""
        return self.image or default_image
