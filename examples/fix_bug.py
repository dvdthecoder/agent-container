"""Example 1 — minimal Python API usage.

Runs an agent task against a public fixture repo, prints the result.

Usage:
    MODAL_TOKEN_ID=... MODAL_TOKEN_SECRET=... GITHUB_TOKEN=... \\
    OPENAI_BASE_URL=... OPENAI_API_KEY=... \\
    python3 examples/fix_bug.py
"""

from sandbox.config import SandboxConfig
from sandbox.sandbox import ModalSandbox
from sandbox.spec import AgentTaskSpec

config = SandboxConfig.from_env()

spec = AgentTaskSpec(
    repo="https://github.com/dvdthecoder/agent-container-fixture",
    task=(
        "The function sum_to_n() in mathlib.py has an off-by-one bug: "
        "it uses range(1, n) but should use range(1, n + 1). "
        "Fix the bug so that all tests in test_mathlib.py pass."
    ),
    backend="opencode",
    base_branch="main",
    create_pr=True,
    run_tests=True,
    timeout_seconds=300,
)

print("Booting sandbox…")
result = ModalSandbox(config).run(spec)

if result.success:
    print(f"Done in {result.duration_seconds:.1f}s")
    print(f"Diff stat : {result.diff_stat}")
    print(f"PR        : {result.pr_url or '(no PR — no diff or token missing)'}")
    if result.tests:
        print(f"Tests     : {result.tests.passed} passed, {result.tests.failed} failed")
else:
    print(f"Failed: {result.error}")
