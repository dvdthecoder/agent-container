"""DevContainer-based agent sandbox: boot → exec → teardown."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from sandbox.config import ConfigError, SandboxConfig
from sandbox.result import AgentTaskResult
from sandbox.spec import AgentTaskSpec

# Name given to our fallback devcontainer spec inside a workspace that
# doesn't already ship a .devcontainer directory.
_DEVCONTAINER_DIR = ".devcontainer"
_DEVCONTAINER_JSON = "devcontainer.json"

# Source of the project-level default spec (relative to this file's package root)
_DEFAULT_SPEC = Path(__file__).parent.parent / _DEVCONTAINER_DIR / _DEVCONTAINER_JSON


def _devcontainer_bin() -> list[str]:
    """Return the argv prefix for the devcontainer CLI."""
    if shutil.which("devcontainer"):
        return ["devcontainer"]
    return ["npx", "--yes", "@devcontainers/cli"]


class DevContainerSandbox:
    """Run an agent task inside an ephemeral dev container.

    Lifecycle per call to :meth:`run`:
      1. Clone repo into a temporary directory
      2. Ensure ``.devcontainer/devcontainer.json`` exists (copy default if absent)
      3. ``devcontainer up``  — build image + start container
      4. ``devcontainer exec`` — run the coding agent
      5. Collect ``git diff`` output
      6. ``devcontainer down`` — stop + remove container (always, even on failure)
    """

    def __init__(self, config: SandboxConfig) -> None:
        self.config = config

    # ------------------------------------------------------------------ public

    def run(self, spec: AgentTaskSpec) -> AgentTaskResult:
        start = time.monotonic()
        tmpdir = tempfile.mkdtemp(prefix="agent-sandbox-")
        workspace = Path(tmpdir)
        try:
            self._clone(spec, workspace)
            self._ensure_devcontainer(workspace, spec)
            self._up(workspace)
            try:
                agent_output, exit_code = self._exec_agent(workspace, spec)
                diff, diff_stat = self._collect_diff(workspace)
            finally:
                self._down(workspace)
        except Exception as exc:
            duration = time.monotonic() - start
            return AgentTaskResult(
                success=False,
                run_id=workspace.name,
                duration_seconds=duration,
                error=str(exc),
                backend=spec.backend,
            )
        finally:
            _rmtree(workspace)

        duration = time.monotonic() - start
        return AgentTaskResult(
            success=exit_code == 0,
            run_id=workspace.name,
            diff=diff,
            diff_stat=diff_stat,
            duration_seconds=duration,
            error=None if exit_code == 0 else agent_output,
            backend=spec.backend,
        )

    # ----------------------------------------------------------------- private

    def _clone(self, spec: AgentTaskSpec, workspace: Path) -> None:
        _run(
            [
                "git",
                "clone",
                "--branch",
                spec.base_branch,
                "--depth",
                "1",
                spec.repo,
                str(workspace),
            ],
            timeout=120,
            error_prefix="git clone failed",
        )

    def _ensure_devcontainer(self, workspace: Path, spec: AgentTaskSpec) -> None:
        dc_dir = workspace / _DEVCONTAINER_DIR
        if (dc_dir / _DEVCONTAINER_JSON).exists():
            return  # repo ships its own spec — use it as-is
        dc_dir.mkdir(exist_ok=True)
        image = spec.resolved_image(self.config.default_image)
        _write_minimal_spec(dc_dir / _DEVCONTAINER_JSON, image)

    def _up(self, workspace: Path) -> None:
        _run(
            [*_devcontainer_bin(), "up", "--workspace-folder", str(workspace)],
            timeout=300,
            error_prefix="devcontainer up failed",
        )

    def _exec_agent(self, workspace: Path, spec: AgentTaskSpec) -> tuple[str, int]:
        task = spec.resolved_task()
        cmd = _agent_command(spec.backend, task)
        result = subprocess.run(
            [*_devcontainer_bin(), "exec", "--workspace-folder", str(workspace), *cmd],
            capture_output=True,
            text=True,
            timeout=spec.timeout_seconds,
        )
        combined = (result.stdout + result.stderr).strip()
        return combined, result.returncode

    def _collect_diff(self, workspace: Path) -> tuple[str, str]:
        diff = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
        stat = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
        return diff, stat.strip()

    def _down(self, workspace: Path) -> None:
        # Best-effort: don't let teardown failure mask the real result
        subprocess.run(
            [*_devcontainer_bin(), "down", "--workspace-folder", str(workspace)],
            capture_output=True,
            timeout=60,
        )


# ------------------------------------------------------------------ helpers


def _run(cmd: list[str], *, timeout: int, error_prefix: str) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ConfigError(f"{error_prefix}:\n{detail}")


def _agent_command(backend: str, task: str) -> list[str]:
    """Return the argv to run the coding agent inside the container."""
    if backend == "opencode":
        return ["opencode", "--print", "-m", task]
    if backend == "claude":
        return ["claude", "--print", task]
    if backend == "gemini":
        return ["gemini", "--yolo", "-p", task]
    if backend == "stub":
        # Used in integration tests — echoes the task, exits 0, makes no changes
        return ["sh", "-c", f"echo {shlex.quote(task)}"]
    raise ValueError(f"Unknown backend: {backend!r}")


def _write_minimal_spec(path: Path, image: str) -> None:
    path.write_text(
        f'{{\n  "name": "agent-sandbox",\n  "image": "{image}"\n}}\n',
        encoding="utf-8",
    )


def _rmtree(path: Path) -> None:
    """Remove a directory tree, ignoring errors (best-effort cleanup)."""
    import shutil as _shutil

    _shutil.rmtree(path, ignore_errors=True)
