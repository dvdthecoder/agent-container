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

        def _emit(event_type: str, **payload) -> None:
            if event_type == "phase":
                elapsed = time.monotonic() - start
                phase = payload.get("phase", "")
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
                agent_output, exit_code = runner.run_agent(
                    sb, backend, spec.resolved_task(), logger=logger
                )
                _emit("log", text=agent_output)

                suite: SuiteResult | None = None
                if exit_code == 0 and spec.run_tests:
                    _emit("phase", phase="TESTING")
                    suite = tester.detect_and_run(sb)

                diff, diff_stat = git_ops.collect_diff(sb)

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
                logger.log("runner", str(exc), level="error")
                logger.finish("error", duration_s=time.monotonic() - start)
                result = AgentTaskResult(
                    success=False,
                    run_id=logger.run_id,
                    duration_seconds=time.monotonic() - start,
                    error=str(exc),
                    backend=spec.backend,
                )
                _emit("done", success=False, error=str(exc), result=result)
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
        oid = sb.object_id
        return oid if isinstance(oid, str) else "unknown"
    except Exception:  # noqa: S110
        return "unknown"
