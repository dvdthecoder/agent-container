"""Measure the token impact of AgentTaskSpec.conventions injection (#155).

Runs the fixture task twice per condition (with / without conventions) against
a branch of the fixture repo that has AGENTS.md removed.  Prints a Markdown
comparison table showing prompt tokens, turn count, think chars stripped, and
cost for each condition.

Usage
-----
    # Ensure the no-agents-md branch exists (one-time setup):
    #   git clone https://github.com/dvdthecoder/agent-container-fixture /tmp/fix
    #   cd /tmp/fix && git checkout -b no-agents-md
    #   git rm AGENTS.md && git commit -m "remove for #155" && git push origin no-agents-md

    python3 scripts/conventions_impact.py
    python3 scripts/conventions_impact.py --runs 3
    python3 scripts/conventions_impact.py --model qwen2.5-coder-32b

Environment (read from .env):
    OPENAI_BASE_URL   required — deployed Modal endpoint
    OPENCODE_MODEL    required — model name (default: qwen2.5-coder-7b)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_root / ".env")

from agent.log_store import RunStore  # noqa: E402
from sandbox.config import SandboxConfig  # noqa: E402
from sandbox.sandbox import ModalSandbox  # noqa: E402
from sandbox.spec import AgentTaskSpec  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FIXTURE_REPO = "https://github.com/dvdthecoder/agent-container-fixture"
_BASE_BRANCH = "no-agents-md"  # branch with AGENTS.md removed

_TASK = (
    "The function sum_to_n() in mathlib.py has an off-by-one bug: "
    "it uses range(1, n) but should use range(1, n + 1). "
    "Fix the bug so that all tests in test_mathlib.py pass."
)

_CONVENTIONS = """\
## Repo structure
- mathlib.py      — math utilities with the bug
- test_mathlib.py — pytest test suite (acceptance criteria)

## Task
Fix the off-by-one bug in sum_to_n() in mathlib.py.

## Acceptance criteria
pytest test_mathlib.py -q passes with no failures.

## Constraints
- Modify only mathlib.py
- Do not add new dependencies
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

COST_PER_1M = 1.00


def _cost(tokens: int | None) -> str:
    if tokens is None:
        return "—"
    return f"${tokens / 1_000_000 * COST_PER_1M:.4f}"


def _fmt(n: int | None) -> str:
    return "—" if n is None else f"{n:,}"


def _check_env() -> SandboxConfig:
    if not os.environ.get("OPENAI_BASE_URL"):
        print("ERROR: OPENAI_BASE_URL not set. Deploy a model first.", file=sys.stderr)
        sys.exit(1)
    if not os.environ.get("OPENCODE_MODEL"):
        print("ERROR: OPENCODE_MODEL not set.", file=sys.stderr)
        sys.exit(1)
    return SandboxConfig.from_env()


def _run_one(config: SandboxConfig, conventions: str | None, run_idx: int) -> dict:
    label = "with conventions" if conventions else "no conventions"
    print(f"  [{run_idx}] {label} ...", flush=True)

    spec = AgentTaskSpec(
        repo=_FIXTURE_REPO,
        task=_TASK,
        backend="opencode",
        base_branch=_BASE_BRANCH,
        create_pr=False,
        run_tests=True,
        conventions=conventions,
        timeout_coldstart=300,
        timeout_agent=600,
        timeout_tests=120,
    )

    t0 = time.monotonic()
    result = ModalSandbox(config).run(spec)
    duration = time.monotonic() - t0

    store = RunStore()
    run_row = store.get_run(result.run_id)
    turns = store.turns(result.run_id)

    total_think = sum(t.think_chars for t in turns)
    all_tools = [tool for t in turns for tool in json.loads(t.tools)]

    return {
        "condition": label,
        "run": run_idx,
        "run_id": result.run_id,
        "success": result.success,
        "prompt_tokens": run_row.prompt_tokens if run_row else None,
        "completion_tokens": run_row.completion_tokens if run_row else None,
        "total_tokens": run_row.total_tokens if run_row else None,
        "turns": len(turns),
        "think_chars": total_think,
        "tools": all_tools,
        "duration": duration,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure conventions injection token impact.")
    parser.add_argument("--runs", type=int, default=2, help="Runs per condition (default: 2)")
    parser.add_argument("--model", default="", help="Override OPENCODE_MODEL")
    args = parser.parse_args()

    if args.model:
        os.environ["OPENCODE_MODEL"] = args.model

    config = _check_env()
    model = os.environ.get("OPENCODE_MODEL", "unknown")

    print(f"\n# Conventions injection impact — model: `{model}`")
    print(f"Repo branch: {_BASE_BRANCH} (AGENTS.md absent)")
    print(f"Runs per condition: {args.runs}\n")

    rows: list[dict] = []

    for condition, conventions in [("no conventions", None), ("with conventions", _CONVENTIONS)]:
        print(f"\n## {condition}")
        for i in range(1, args.runs + 1):
            row = _run_one(config, conventions, i)
            rows.append(row)
            status = "✅" if row["success"] else "❌"
            print(
                f"    {status}  prompt={_fmt(row['prompt_tokens'])}  "
                f"turns={row['turns']}  think_chars={row['think_chars']}  "
                f"{row['duration']:.0f}s",
                flush=True,
            )

    # ── Results table ─────────────────────────────────────────────────────────
    print("\n\n## Results\n")
    print(
        "| Condition | Run | Success | Prompt tok | Completion tok"
        " | Turns | Think chars | Est. cost | Duration |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(
            f"| {r['condition']} | {r['run']} | {'✅' if r['success'] else '❌'}"
            f" | {_fmt(r['prompt_tokens'])} | {_fmt(r['completion_tokens'])}"
            f" | {r['turns']} | {r['think_chars']:,}"
            f" | {_cost(r['total_tokens'])} | {r['duration']:.0f}s |"
        )

    # ── Per-condition averages ────────────────────────────────────────────────
    print("\n## Summary\n")
    for condition in ("no conventions", "with conventions"):
        subset = [r for r in rows if r["condition"] == condition]
        successes = sum(1 for r in subset if r["success"])
        prompt_avg = (
            int(sum(r["prompt_tokens"] for r in subset if r["prompt_tokens"]) / len(subset))
            if subset
            else None
        )
        turns_avg = sum(r["turns"] for r in subset) / len(subset) if subset else 0
        think_avg = int(sum(r["think_chars"] for r in subset) / len(subset)) if subset else 0
        dur_avg = sum(r["duration"] for r in subset) / len(subset) if subset else 0
        print(
            f"- **{condition}**: {successes}/{len(subset)} succeeded"
            f"  ·  avg prompt {_fmt(prompt_avg)} tok"
            f"  ·  avg {turns_avg:.1f} turns"
            f"  ·  avg {think_avg:,} think chars"
            f"  ·  avg {dur_avg:.0f}s"
        )

    # ── Delta ─────────────────────────────────────────────────────────────────
    no_conv = [r for r in rows if r["condition"] == "no conventions"]
    with_conv = [r for r in rows if r["condition"] == "with conventions"]
    if no_conv and with_conv:

        def _avg_prompt(rs: list[dict]) -> float:
            vals = [r["prompt_tokens"] for r in rs if r["prompt_tokens"]]
            return sum(vals) / len(vals) if vals else 0

        def _avg_turns(rs: list[dict]) -> float:
            return sum(r["turns"] for r in rs) / len(rs)

        p_delta = _avg_prompt(with_conv) - _avg_prompt(no_conv)
        t_delta = _avg_turns(with_conv) - _avg_turns(no_conv)
        print("\n### Delta (with − no conventions)")
        print(f"- Prompt tokens: {p_delta:+,.0f}")
        print(f"- Turns: {t_delta:+.1f}")
        net = "positive" if p_delta < 0 else "negative" if p_delta > 0 else "neutral"
        verdict = "conventions add tokens; turns delta shows if they save more"
        print(f"- Frugal principle: **{net}** ({verdict})")

    # ── Per-run tool call trace ───────────────────────────────────────────────
    print("\n## Tool call trace\n")
    for r in rows:
        print(f"- {r['condition']} run {r['run']} ({r['run_id']}): {r['tools']}")


if __name__ == "__main__":
    main()
