from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class SuiteResult:
    """Structured output from the test suite that ran inside the workspace."""

    passed: int
    failed: int
    output: str
    runner_name: str | None = None

    @property
    def success(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "failed": self.failed,
            "output": self.output,
            "runner_name": self.runner_name,
        }


@dataclass
class AgentTaskResult:
    """Output contract returned after every agent sandbox run."""

    success: bool
    run_id: str
    branch: str | None = None
    pr_url: str | None = None
    diff: str | None = None
    diff_stat: str | None = None
    tests: TestResult | None = None
    duration_seconds: float = 0.0
    error: str | None = None
    backend: str = "opencode"

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "run_id": self.run_id,
            "branch": self.branch,
            "pr_url": self.pr_url,
            "diff": self.diff,
            "diff_stat": self.diff_stat,
            "tests": self.tests.to_dict() if self.tests else None,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
            "backend": self.backend,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)
