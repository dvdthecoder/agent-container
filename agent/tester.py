"""Auto-detect and run the project test suite inside a Modal sandbox workspace.

Detection order: pytest → npm → cargo → go.  The first matching runner wins.
Returns ``None`` if no recognisable test configuration is found.
"""

from __future__ import annotations

import re

import modal
from sandbox.result import SuiteResult

# ── Detection ────────────────────────────────────────────────────────────────

# Shell fragment that evaluates to true when the runner's marker file exists.
_DETECT_CONDITIONS: list[tuple[str, str]] = [
    (
        "pytest",
        "[ -f pytest.ini ] || [ -f setup.cfg ]"
        " || ([ -f pyproject.toml ] && grep -q 'tool.pytest' pyproject.toml 2>/dev/null)"
        " || (find . -maxdepth 2 -name 'test_*.py' | grep -q . 2>/dev/null)",
    ),
    ("npm", "[ -f package.json ]"),
    ("cargo", "[ -f Cargo.toml ]"),
    ("go", "[ -f go.mod ]"),
]

# Command to run for each runner (executed in /workspace).
_RUN_COMMANDS: dict[str, list[str]] = {
    "pytest": ["python", "-m", "pytest", "--tb=short", "-q"],
    "npm": ["npm", "test", "--", "--no-coverage"],
    "cargo": ["cargo", "test"],
    "go": ["go", "test", "./..."],
}

# ── Public API ────────────────────────────────────────────────────────────────


def detect_and_run(
    sb: modal.Sandbox,
    workdir: str = "/workspace",
) -> SuiteResult | None:
    """Detect the test runner in *workdir* and run the suite.

    Returns a :class:`~sandbox.result.SuiteResult` on success or failure, or
    ``None`` if no test runner configuration was found.
    """
    runner_name = _detect_runner(sb, workdir)
    if runner_name is None:
        return None
    return _run_tests(sb, runner_name, workdir)


# ── Private helpers ───────────────────────────────────────────────────────────


def _detect_runner(sb: modal.Sandbox, workdir: str) -> str | None:
    """Return the name of the first matching test runner, or ``None``."""
    # Build a single sh -c command that echoes the runner name or "none".
    branches = " ".join(f"if {cond}; then echo {name}; el" for name, cond in _DETECT_CONDITIONS)
    # Close the if-elif chain.
    script = branches + "se echo none; fi"

    proc = sb.exec("sh", "-c", script, workdir=workdir)
    output = proc.stdout.read().strip()
    proc.wait()

    return None if output == "none" else output


def _run_tests(
    sb: modal.Sandbox,
    runner_name: str,
    workdir: str,
) -> SuiteResult:
    """Run the test suite and return a structured result."""
    cmd = _RUN_COMMANDS[runner_name]
    proc = sb.exec(*cmd, workdir=workdir)
    output = proc.stdout.read()
    proc.wait()
    exit_code = proc.returncode

    passed, failed = _parse_counts(runner_name, output, exit_code)
    return SuiteResult(
        passed=passed,
        failed=failed,
        output=output,
        runner_name=runner_name,
    )


def _parse_counts(runner_name: str, output: str, exit_code: int) -> tuple[int, int]:
    """Extract (passed, failed) counts from test output, falling back to exit code."""
    if runner_name in ("pytest", "npm"):
        passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", output)) else 0
        failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", output)) else 0
        # If we couldn't parse counts, infer from exit code.
        if passed == 0 and failed == 0:
            return (1, 0) if exit_code == 0 else (0, 1)
        return passed, failed

    if runner_name == "cargo":
        # "test result: ok. 5 passed; 0 failed"
        m = re.search(r"(\d+) passed; (\d+) failed", output)
        if m:
            return int(m.group(1)), int(m.group(2))
        return (1, 0) if exit_code == 0 else (0, 1)

    # go — "ok  github.com/..." or "FAIL\t..." — use exit code only.
    return (1, 0) if exit_code == 0 else (0, 1)
