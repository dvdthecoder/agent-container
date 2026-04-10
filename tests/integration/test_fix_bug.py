"""Integration tests: agent fixes deliberate off-by-one bug in fixture repo.

Fixture repo: https://github.com/dvdthecoder/agent-container-fixture
Bug: sum_to_n() uses range(1, n) instead of range(1, n + 1)

Test modes
----------
stub  (marker: integration) — runs on every PR via integration.yml
    Uses backend="stub".  The stub agent only echoes the task — it does NOT
    actually fix the code, so we assert the sandbox lifecycle works end-to-end
    (sandbox created, cloned, agent ran, diff collected, sandbox torn down)
    without making any LLM calls or spending money.

real  (marker: e2e) — runs nightly via e2e.yml
    Uses backend="opencode" against the Modal model endpoint.
    Asserts the agent produced a diff that includes `range(1, n + 1)`.
"""

from __future__ import annotations

import os

import pytest

from sandbox.config import SandboxConfig
from sandbox.sandbox import ModalSandbox
from sandbox.spec import AgentTaskSpec

FIXTURE_REPO = os.getenv(
    "FIXTURE_REPO",
    "https://github.com/dvdthecoder/agent-container-fixture",
)

FIX_TASK = (
    "The function sum_to_n() in mathlib.py has an off-by-one bug: "
    "it uses range(1, n) but should use range(1, n + 1). "
    "Fix the bug so that all tests in test_mathlib.py pass."
)


# ──────────────────────────────────────────────────────────── stub (integration)


@pytest.mark.integration
def test_stub_agent_runs_without_error():
    """Sandbox lifecycle works end-to-end with the stub backend.

    The stub agent echoes the task text — it does not modify any code — so
    result.success reflects the agent's exit code (0), not whether the bug
    was fixed.  The important thing is that Modal created the sandbox, cloned
    the fixture repo, ran the agent, collected the diff, and tore down cleanly.
    """
    config = SandboxConfig.from_env()
    spec = AgentTaskSpec(
        repo=FIXTURE_REPO,
        task=FIX_TASK,
        backend="stub",
        base_branch="main",
        timeout_seconds=120,
    )

    result = ModalSandbox(config).run(spec)

    assert result.success is True, f"Stub agent failed: {result.error}"
    assert result.run_id != "unknown", "Sandbox did not get an object_id"
    assert result.duration_seconds > 0
    assert result.backend == "stub"
    # Stub doesn't touch the code, so diff is empty
    assert result.diff == ""


# ──────────────────────────────────────────────────────────── real (e2e / nightly)


@pytest.mark.e2e
def test_opencode_agent_fixes_off_by_one():
    """Real opencode agent fixes the bug and produces the correct diff.

    Requires:
    - MODAL_TOKEN_ID / MODAL_TOKEN_SECRET in environment
    - OPENAI_BASE_URL pointing to the Modal model endpoint
    - OPENAI_API_KEY set to "modal"
    - OPENCODE_MODEL set to "qwen3-coder"
    """
    config = SandboxConfig.from_env()
    spec = AgentTaskSpec(
        repo=FIXTURE_REPO,
        task=FIX_TASK,
        backend="opencode",
        base_branch="main",
        timeout_seconds=300,
    )

    result = ModalSandbox(config).run(spec)

    assert result.success is True, f"Agent failed:\n{result.error}"
    assert result.diff, "Agent produced no diff — bug was not fixed"
    assert "n + 1" in result.diff or "n+1" in result.diff, (
        f"Expected fix (range(1, n + 1)) not found in diff:\n{result.diff}"
    )
    assert result.diff_stat, "diff --stat is empty"
