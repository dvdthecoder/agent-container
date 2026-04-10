"""Modal-based agent sandbox: boot → exec → teardown."""

from __future__ import annotations

import shlex
import time
import uuid

import modal
from sandbox.config import ConfigError, SandboxConfig
from sandbox.result import AgentTaskResult
from sandbox.spec import AgentTaskSpec

# Base image — git + Node (for opencode) + Python pre-installed.
# Built once by Modal and cached; subsequent runs reuse the cached layer.
_BASE_IMAGE = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "curl")
    .run_commands(
        "curl -fsSL https://deb.nodesource.com/setup_lts.x | bash -",
        "apt-get install -y nodejs",
        "npm install -g opencode-ai",
    )
)


class ModalSandbox:
    """Run an agent task inside an ephemeral Modal sandbox.

    Lifecycle per call to :meth:`run`:
      1. Create a Modal sandbox with the configured image
      2. Clone the target repo inside the sandbox
      3. Run the coding agent (opencode / claude / gemini / stub)
      4. Collect ``git diff`` output
      5. Terminate the sandbox (always, even on failure)
    """

    def __init__(self, config: SandboxConfig) -> None:
        self.config = config

    # ------------------------------------------------------------------ public

    def run(self, spec: AgentTaskSpec) -> AgentTaskResult:
        start = time.monotonic()
        sb: modal.Sandbox | None = None
        # Unique app per run — avoids collisions when multiple runs execute in parallel.
        app = modal.App.lookup(f"agent-container-{uuid.uuid4().hex[:8]}", create_if_missing=True)
        try:
            try:
                sb = self._create(spec, app)
                self._clone(sb, spec)
                agent_output, exit_code = self._exec_agent(sb, spec)
                diff, diff_stat = self._collect_diff(sb)
            except Exception as exc:
                return AgentTaskResult(
                    success=False,
                    run_id=_run_id(sb),
                    duration_seconds=time.monotonic() - start,
                    error=str(exc),
                    backend=spec.backend,
                )

            return AgentTaskResult(
                success=exit_code == 0,
                run_id=_run_id(sb),
                diff=diff,
                diff_stat=diff_stat,
                duration_seconds=time.monotonic() - start,
                error=None if exit_code == 0 else agent_output,
                backend=spec.backend,
            )
        finally:
            # Always terminate — even on KeyboardInterrupt or other BaseException.
            _terminate(sb)

    # ----------------------------------------------------------------- private

    def _create(self, spec: AgentTaskSpec, app: modal.App) -> modal.Sandbox:
        image = _BASE_IMAGE
        if spec.image:
            image = modal.Image.from_registry(spec.image)
        # Merge config-level env vars (model endpoint, git tokens) with
        # any task-specific overrides from spec.env.
        env = {**self.config.container_env(), **spec.env}
        return modal.Sandbox.create(
            image=image,
            timeout=spec.timeout_seconds,
            secrets=[modal.Secret.from_dict(env)] if env else [],
            app=app,
        )

    def _clone(self, sb: modal.Sandbox, spec: AgentTaskSpec) -> None:
        proc = sb.exec(
            "git",
            "clone",
            "--branch",
            spec.base_branch,
            "--depth",
            "1",
            spec.repo,
            "/workspace",
        )
        proc.wait()
        if proc.returncode != 0:
            raise ConfigError(f"git clone failed:\n{proc.stderr.read()}")

    def _exec_agent(self, sb: modal.Sandbox, spec: AgentTaskSpec) -> tuple[str, int]:
        cmd = _agent_command(spec.backend, spec.resolved_task())
        proc = sb.exec(*cmd, workdir="/workspace")
        output = proc.stdout.read()
        proc.wait()
        return output, proc.returncode

    def _collect_diff(self, sb: modal.Sandbox) -> tuple[str, str]:
        diff_proc = sb.exec("git", "diff", "HEAD", workdir="/workspace")
        diff = diff_proc.stdout.read()
        diff_proc.wait()

        stat_proc = sb.exec("git", "diff", "--stat", "HEAD", workdir="/workspace")
        stat = stat_proc.stdout.read()
        stat_proc.wait()

        return diff, stat.strip()


# ------------------------------------------------------------------ helpers


def _agent_command(backend: str, task: str) -> list[str]:
    """Return the argv to run the coding agent inside the sandbox."""
    if backend == "opencode":
        return ["opencode", "--print", "-m", task]
    if backend == "claude":
        return ["claude", "--print", task]
    if backend == "gemini":
        return ["gemini", "--yolo", "-p", task]
    if backend == "stub":
        return ["sh", "-c", f"echo {shlex.quote(task)}"]
    raise ValueError(f"Unknown backend: {backend!r}")


def _terminate(sb: modal.Sandbox | None) -> None:
    """Terminate sandbox, ignoring errors (best-effort cleanup)."""
    if sb is None:
        return
    try:
        sb.terminate()
    except Exception:  # noqa: S110
        pass  # best-effort — don't let teardown errors propagate


def _run_id(sb: modal.Sandbox | None) -> str:
    if sb is None:
        return "unknown"
    try:
        return sb.object_id
    except Exception:  # noqa: S110
        return "unknown"
