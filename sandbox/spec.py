from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


def _expand_task_spec(raw: str) -> str:
    """Expand a YAML task spec into a structured prompt string.

    Recognised keys:
      task                 (required) — the task description
      acceptance_criteria  (optional) — how to verify success
      constraints          (optional) — list of constraints or a single string
      context_files        (optional) — list of relevant file paths

    Returns *raw* unchanged if it is not valid YAML, if the parsed value is
    not a dict, or if the dict has no ``task`` key.
    """
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw

    if not isinstance(data, dict) or "task" not in data:
        return raw

    parts = [f"## Task\n{data['task']}"]

    if data.get("acceptance_criteria"):
        parts.append(f"## Acceptance Criteria\n{data['acceptance_criteria']}")

    if data.get("constraints"):
        constraints = data["constraints"]
        if isinstance(constraints, list):
            bullet = "\n".join(f"- {c}" for c in constraints)
            parts.append(f"## Constraints\n{bullet}")
        else:
            parts.append(f"## Constraints\n{constraints}")

    if data.get("context_files"):
        files = data["context_files"]
        if isinstance(files, list):
            bullet = "\n".join(f"- {f}" for f in files)
            parts.append(f"## Relevant Files\n{bullet}")
        else:
            parts.append(f"## Relevant Files\n{files}")

    return "\n\n".join(parts)


@dataclass
class AgentTaskSpec:
    """Input contract for a single agent sandbox run."""

    repo: str
    task: str | None = None
    task_file: Path | None = None
    base_branch: str = "main"
    image: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    # Per-phase timeouts (seconds).  Use these instead of the deprecated
    # timeout_seconds alias.
    timeout_coldstart: int = 300  # warmup probe: poll serve endpoint until ready
    timeout_agent: int = 600  # agent execution budget (passed to OPENCODE_TIMEOUT)
    timeout_tests: int = 120  # test suite execution budget
    # Deprecated: sets timeout_agent if non-zero.  Kept for CLI / dashboard compat.
    timeout_seconds: int = 0
    cpu: float = 2.0  # vCPUs allocated to the sandbox (Modal default 0.1 is too low)
    memory: int = 1024  # MB of RAM for the sandbox (Modal default 128 MB causes OOM)
    create_pr: bool = True
    run_tests: bool = True  # auto-detect and run the project test suite
    backend: str = "opencode"  # opencode | claude | gemini
    initiated_by: str = "cli"  # cli | dashboard
    run_id: str | None = None  # pre-allocated run ID (dashboard sets this)
    conventions: str | None = None  # fallback AGENTS.md content for repos that don't have one

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

        # Backwards compat: timeout_seconds overrides timeout_agent.
        if self.timeout_seconds > 0:
            self.timeout_agent = self.timeout_seconds

        for name, val in (
            ("timeout_coldstart", self.timeout_coldstart),
            ("timeout_agent", self.timeout_agent),
            ("timeout_tests", self.timeout_tests),
        ):
            if val < 1:
                raise ValueError(f"{name} must be >= 1, got {val}")

    @property
    def total_timeout(self) -> int:
        """Total sandbox lifetime: coldstart + agent + tests."""
        return self.timeout_coldstart + self.timeout_agent + self.timeout_tests

    def resolved_task(self) -> str:
        """Return the raw task string, reading from file if task_file was provided.

        Use this for logging, PR titles, and other places that need a short
        human-readable description.  For the agent prompt use resolved_prompt().
        """
        if self.task is not None:
            return self.task
        return self.task_file.read_text(encoding="utf-8").strip()  # type: ignore[union-attr]

    def resolved_prompt(self) -> str:
        """Return the formatted agent prompt, expanding YAML task specs.

        If the task string is a YAML document containing a ``task`` key, it is
        expanded into a structured prompt with labelled sections:

            ## Task
            <task description>

            ## Acceptance Criteria        (if provided)
            <criteria>

            ## Constraints                (if provided)
            - constraint 1
            - constraint 2

            ## Relevant Files             (if provided)
            - path/to/file.py

        Plain-text tasks (no YAML, or YAML without a ``task`` key) are returned
        unchanged so existing callers are unaffected.
        """
        return _expand_task_spec(self.resolved_task())

    def resolved_image(self, default_image: str) -> str:
        """Return the Docker image to use, falling back to SandboxConfig.default_image."""
        return self.image or default_image
