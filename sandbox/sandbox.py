"""Modal-based agent sandbox: boot → exec → teardown.

ModalSandbox is the orchestrator.  Domain logic lives in dedicated modules:
  agent.git_ops  — clone, diff, push, PR
  agent.runner   — invoke the coding agent backend
  agent.tester   — detect and run the project test suite
  agent.backends — per-backend command construction
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable

import modal
from agent import git_ops, runner, tester
from agent.backends import get_backend
from agent.log_store import RunLogger
from sandbox.config import SandboxConfig
from sandbox.result import AgentTaskResult, SuiteResult
from sandbox.spec import AgentTaskSpec


class PhaseError(Exception):
    """Raised when a sandbox phase fails.  Carries phase name and elapsed time
    so the error message is self-contained without inspecting run state.
    """

    def __init__(self, phase: str, reason: str, elapsed: float) -> None:
        self.phase = phase
        self.reason = reason
        self.elapsed = elapsed
        super().__init__(str(self))

    def __str__(self) -> str:
        return f"[{self.phase}] {self.reason} (after {self.elapsed:.1f}s)"


# Base image — git + aider + opencode (Node) pre-installed.
# Built once by Modal and cached; subsequent runs reuse the cached layer.
#
# Both agent backends are installed so either can be selected at runtime:
#   --backend aider    (default) direct Chat Completions, no proxy
#   --backend opencode           Responses API adapter, multi-turn loop
_BASE_IMAGE = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "curl")
    # aider — direct Chat Completions, no proxy needed (Phase 1 default)
    .pip_install("aider-chat")
    # opencode — requires Node.js (Phase 2, Responses API adapter)
    .run_commands(
        "curl -fsSL https://deb.nodesource.com/setup_lts.x | bash -",
        "apt-get install -y nodejs",
        "npm install -g opencode-ai",
    )
    .add_local_file("agent/aider_runner.py", "/aider_runner.py", copy=True)
    .add_local_file("agent/opencode_runner.py", "/opencode_runner.py", copy=True)
)


class ModalSandbox:
    """Run an agent task inside an ephemeral Modal sandbox.

    Lifecycle per call to :meth:`run`:
      1. Create a Modal sandbox with the configured image
      2. Clone the target repo
      3. Run the coding agent backend
      4. Run the project test suite (if ``spec.run_tests`` is True)
      5. Collect ``git diff`` output
      6. Push branch and open PR (if ``spec.create_pr`` is True and diff is non-empty)
      7. Terminate the sandbox (always, even on failure)
    """

    def __init__(self, config: SandboxConfig) -> None:
        self.config = config

    # ------------------------------------------------------------------ public

    def run(
        self,
        spec: AgentTaskSpec,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> AgentTaskResult:
        """Run the agent pipeline.

        Args:
            spec: Task specification.
            on_event: Optional callback invoked as ``on_event(event_type, payload)``
                      at each lifecycle transition.  Used by the dashboard to stream
                      progress without coupling sandbox logic to FastAPI.
        """

        logger = RunLogger.create(
            repo=spec.repo,
            task=spec.resolved_task(),
            backend=spec.backend,
        )

        current_phase: list[str] = ["INIT"]  # mutable cell so _emit can update it

        def _emit(event_type: str, **payload) -> None:
            if event_type == "phase":
                elapsed = time.monotonic() - start
                phase = payload.get("phase", "")
                current_phase[0] = phase
                print(
                    f"[sandbox] phase={phase}  elapsed={elapsed:.1f}s", file=sys.stderr, flush=True
                )  # noqa: E501
                logger.phase(phase)
            if on_event is not None:
                try:
                    on_event(event_type, payload)
                except Exception:  # noqa: S110
                    pass  # never let dashboard callbacks crash the run

        start = time.monotonic()
        sb: modal.Sandbox | None = None
        try:
            try:
                _emit("phase", phase="BOOTING")
                sb = self._create(spec)
                logger.set_sandbox_id(_run_id(sb))

                _emit("phase", phase="CLONING")
                git_ops.clone(sb, spec.repo, spec.base_branch)

                _emit("phase", phase="RUNNING")
                backend = get_backend(spec.backend)
                # Give the agent spec.timeout_seconds - 60s (same headroom
                # given to OPENCODE_TIMEOUT) before we hard-terminate.
                agent_timeout = float(spec.timeout_seconds - 60)
                agent_output, exit_code = runner.run_agent(
                    sb, backend, spec.resolved_task(), logger=logger, timeout=agent_timeout
                )
                _emit("log", text=agent_output)

                suite: SuiteResult | None = None
                if exit_code == 0 and spec.run_tests:
                    _emit("phase", phase="TESTING")
                    suite = tester.detect_and_run(sb)

                diff, diff_stat = git_ops.collect_diff(sb, base_branch=spec.base_branch)

                branch: str | None = None
                pr_url: str | None = None
                if exit_code == 0 and diff and spec.create_pr:
                    _emit("phase", phase="PR")
                    branch, pr_url = git_ops.push_and_pr(
                        sb,
                        repo=spec.repo,
                        base_branch=spec.base_branch,
                        backend=spec.backend,
                        task=spec.resolved_task(),
                        config=self.config,
                    )

            except Exception as exc:
                elapsed = time.monotonic() - start
                # Wrap bare exceptions with phase + elapsed context so callers
                # know where the run failed without parsing logs.
                if not isinstance(exc, PhaseError):
                    exc = PhaseError(current_phase[0], str(exc), elapsed)
                error_msg = str(exc)
                # Terminate immediately — don't wait for Modal's timeout to
                # bill us for idle CPU.  The finally block will no-op if sb
                # is already None or already terminated.
                _terminate(sb)
                sb = None  # prevent double-terminate in finally
                logger.log("runner", error_msg, level="error")
                logger.finish("error", duration_s=elapsed)
                result = AgentTaskResult(
                    success=False,
                    run_id=logger.run_id,
                    duration_seconds=elapsed,
                    error=error_msg,
                    backend=spec.backend,
                )
                _emit("done", success=False, error=error_msg, result=result)
                return result

            outcome = "success" if exit_code == 0 else "failed"
            duration = time.monotonic() - start
            logger.finish(outcome, branch=branch, pr_url=pr_url, duration_s=duration)
            result = AgentTaskResult(
                success=exit_code == 0,
                run_id=logger.run_id,
                branch=branch,
                pr_url=pr_url,
                diff=diff,
                diff_stat=diff_stat,
                tests=suite,
                duration_seconds=duration,
                error=None if exit_code == 0 else agent_output,
                backend=spec.backend,
            )
            _emit(
                "done",
                success=result.success,
                pr_url=pr_url,
                diff_stat=diff_stat,
                error=result.error,
                result=result,
            )
            return result
        finally:
            # Always terminate — even on KeyboardInterrupt or other BaseException.
            _terminate(sb)
            logger.close()

    # ----------------------------------------------------------------- private

    def _create(self, spec: AgentTaskSpec) -> modal.Sandbox:
        image = _BASE_IMAGE
        if spec.image:
            image = modal.Image.from_registry(spec.image)
        # Merge config-level env vars (model endpoint, git tokens) with
        # any task-specific overrides from spec.env.
        env = {**self.config.container_env(), **spec.env}
        # Give the opencode runner 60s less than the sandbox timeout so it
        # can exit cleanly before Modal forcefully kills the container.
        env.setdefault("OPENCODE_TIMEOUT", str(spec.timeout_seconds - 60))
        # Single shared app — all sandbox runs attach to the same app so
        # Modal doesn't accumulate one app per run (the original leak).
        app = modal.App.lookup("agent-container-sandbox", create_if_missing=True)
        return modal.Sandbox.create(
            image=image,
            timeout=spec.timeout_seconds,
            secrets=[modal.Secret.from_dict(env)] if env else [],
            app=app,
            cpu=spec.cpu,
            memory=spec.memory,
        )


# ------------------------------------------------------------------ helpers


def _terminate(sb: modal.Sandbox | None) -> None:
    """Terminate sandbox, logging outcome but never propagating errors."""
    if sb is None:
        return
    try:
        sb.terminate()
        print("[sandbox] container terminated", file=sys.stderr, flush=True)
    except Exception as exc:  # noqa: BLE001
        # Log so we know terminate failed — still best-effort, never raise.
        print(f"[sandbox] terminate failed: {exc}", file=sys.stderr, flush=True)


def _run_id(sb: modal.Sandbox | None) -> str:
    if sb is None:
        return "unknown"
    try:
        oid = sb.object_id
        return oid if isinstance(oid, str) else "unknown"
    except Exception:  # noqa: S110
        return "unknown"
