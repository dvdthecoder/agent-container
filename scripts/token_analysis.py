"""Token / cost / quality analysis across backends and task tiers.

Fires a configurable set of runs against the fixture repo, waits for each to
finish, then prints a Markdown summary table showing token usage, estimated
cost, success rate, and wall-clock duration for every (backend, model) pair.

Usage
-----
    make test-analysis                        # default: aider + opencode, 1 run each
    make test-analysis BACKENDS=aider RUNS=3  # 3 aider runs
    make test-analysis BACKENDS=opencode COST_PER_1M=0.80

Environment (read from .env or shell):
    OPENAI_BASE_URL       required — deployed Modal endpoint
    OPENAI_API_KEY        default: modal
    OPENCODE_MODEL        required — served model name (e.g. qwen2.5-coder-32b)
    GITHUB_TOKEN          optional — enables PR creation in analysis runs
    ANALYSIS_BACKENDS     comma-sep backends to run (default: aider,opencode)
    ANALYSIS_RUNS         runs per backend (default: 1)
    ANALYSIS_COST_PER_1M  USD per 1M tokens for cost estimate (default: 1.00)
    ANALYSIS_NO_PR        set to 1 to skip PR creation (faster, cheaper)
    ANALYSIS_MODEL_LABEL  human-readable model label for tables/sidecars
                          (default: value of OPENCODE_MODEL)
    ANALYSIS_ENDPOINT     override OPENAI_BASE_URL for this run — useful when
                          pointing at an isolated per-model app endpoint
    ANALYSIS_OUTPUT_JSON  path to write a structured JSON sidecar alongside the
                          Markdown output (used by combine_analysis.py)

Output
------
Prints a Markdown table to stdout and a summary line.  Pipe to a file:
    make test-analysis > docs/analysis/$(date +%Y-%m-%d).md

Sidecar JSON (written when ANALYSIS_OUTPUT_JSON is set):
    {
      "model_label": "Qwen3 8B · A10G",
      "cost_per_1m": 1.00,
      "task": "...",
      "rows": [{"backend": "aider", "run": 1, "success": true, ...}, ...]
    }
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# ── bootstrap — add project root to path ─────────────────────────────────────
_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_root / ".env")

from agent.log_store import RunStore  # noqa: E402
from sandbox.config import SandboxConfig  # noqa: E402
from sandbox.sandbox import ModalSandbox  # noqa: E402
from sandbox.spec import AgentTaskSpec  # noqa: E402

# ── config from env ───────────────────────────────────────────────────────────
_FIXTURE_REPO = "https://github.com/dvdthecoder/agent-container-fixture"
_TASK = (
    "The function sum_to_n() in mathlib.py has an off-by-one bug: "
    "it uses range(1, n) but should use range(1, n + 1). "
    "Fix the bug so that all tests in test_mathlib.py pass."
)

_raw_backends = os.environ.get("ANALYSIS_BACKENDS", "aider,opencode")
BACKENDS: list[str] = [b.strip() for b in _raw_backends.split(",") if b.strip()]
RUNS_PER_BACKEND: int = int(os.environ.get("ANALYSIS_RUNS", "1"))
COST_PER_1M: float = float(os.environ.get("ANALYSIS_COST_PER_1M", "1.00"))
NO_PR: bool = os.environ.get("ANALYSIS_NO_PR", "0") == "1"
# Optional: override the endpoint URL for this run (isolated app for a specific model).
ANALYSIS_ENDPOINT: str = os.environ.get("ANALYSIS_ENDPOINT", "")
# Human-readable label printed in tables and written to the JSON sidecar.
# Defaults to OPENCODE_MODEL so it's always meaningful even if not set.
ANALYSIS_MODEL_LABEL: str = os.environ.get("ANALYSIS_MODEL_LABEL", "")
# If set, write a structured JSON sidecar to this path.
ANALYSIS_OUTPUT_JSON: str = os.environ.get("ANALYSIS_OUTPUT_JSON", "")


def _check_env() -> SandboxConfig:
    # ANALYSIS_ENDPOINT overrides OPENAI_BASE_URL for this run (isolated app).
    if ANALYSIS_ENDPOINT:
        os.environ["OPENAI_BASE_URL"] = ANALYSIS_ENDPOINT

    base_url = os.environ.get("OPENAI_BASE_URL", "")
    model = os.environ.get("OPENCODE_MODEL", "")
    if not base_url:
        print("ERROR: OPENAI_BASE_URL is not set — deploy a model first.", file=sys.stderr)
        print("  modal deploy modal/serve.py", file=sys.stderr)
        sys.exit(1)
    if not model:
        print("ERROR: OPENCODE_MODEL is not set.", file=sys.stderr)
        print("  export OPENCODE_MODEL=qwen2.5-coder-32b", file=sys.stderr)
        sys.exit(1)
    return SandboxConfig.from_env()


# ── result accumulator ────────────────────────────────────────────────────────

_HEADER = (
    "| Backend | Run | Success | Prompt tok | Completion tok | Total tok "
    "| Est. cost | Duration | PR |"
)
_SEP = "|---|---|---|---|---|---|---|---|---|"


def _cost(tokens: int) -> str:
    usd = tokens / 1_000_000 * COST_PER_1M
    return f"${usd:.4f}"


def _fmt(n: int | None) -> str:
    if n is None:
        return "—"
    return f"{n:,}"


def main() -> None:
    config = _check_env()
    model = os.environ.get("OPENCODE_MODEL", "unknown")
    model_label = ANALYSIS_MODEL_LABEL or model

    print(f"\n# Token Analysis — model: `{model_label}`\n", flush=True)
    print(f"Backends: {', '.join(BACKENDS)}  |  Runs per backend: {RUNS_PER_BACKEND}", flush=True)
    print(f"Cost rate: ${COST_PER_1M:.2f} / 1M tokens  |  Create PR: {not NO_PR}\n", flush=True)

    rows: list[dict] = []
    run_num = 0

    for backend in BACKENDS:
        for i in range(RUNS_PER_BACKEND):
            run_num += 1
            label = f"{backend} #{i + 1}"
            print(f"[{run_num}/{len(BACKENDS) * RUNS_PER_BACKEND}] Firing {label} ...", flush=True)

            spec = AgentTaskSpec(
                repo=_FIXTURE_REPO,
                task=_TASK,
                backend=backend,
                base_branch="main",
                create_pr=not NO_PR,
                run_tests=True,
                timeout_coldstart=300,
                timeout_agent=600,
                timeout_tests=120,
            )

            t0 = time.monotonic()
            result = ModalSandbox(config).run(spec)
            dur = time.monotonic() - t0

            # Token data is persisted to SQLite by the logger inside sandbox.run().
            # Read it back using run_id so the script doesn't need changes to
            # AgentTaskResult's public API.
            store = RunStore()
            run_row = store.get_run(result.run_id)
            prompt_tok = run_row.prompt_tokens if run_row else None
            completion_tok = run_row.completion_tokens if run_row else None
            total_tok = run_row.total_tokens if run_row else None

            rows.append(
                {
                    "backend": backend,
                    "run": i + 1,
                    "success": result.success,
                    "prompt_tokens": prompt_tok,
                    "completion_tokens": completion_tok,
                    "total_tokens": total_tok,
                    "duration": dur,
                    "pr_url": result.pr_url or "",
                    "run_id": result.run_id,
                }
            )

            status = "✅" if result.success else "❌"
            tok = _fmt(total_tok)
            print(f"  {status}  {tok} tokens  {dur:.0f}s  {result.pr_url or '(no PR)'}", flush=True)

    # ── summary table ─────────────────────────────────────────────────────────
    print("\n## Results\n")
    print(_HEADER)
    print(_SEP)
    for r in rows:
        success = "✅" if r["success"] else "❌"
        prompt = _fmt(r["prompt_tokens"])
        completion = _fmt(r["completion_tokens"])
        total = _fmt(r["total_tokens"])
        cost = _cost(r["total_tokens"]) if r["total_tokens"] is not None else "—"
        dur = f"{r['duration']:.0f}s"
        pr = f"[PR]({r['pr_url']})" if r["pr_url"] else "—"
        print(
            f"| {r['backend']} | {r['run']} | {success} "
            f"| {prompt} | {completion} | {total} | {cost} | {dur} | {pr} |"
        )

    # ── aggregate stats ───────────────────────────────────────────────────────
    print("\n## Summary\n")
    for backend in BACKENDS:
        subset = [r for r in rows if r["backend"] == backend]
        successes = sum(1 for r in subset if r["success"])
        totals = [r["total_tokens"] for r in subset if r["total_tokens"] is not None]
        avg_tokens = int(sum(totals) / len(totals)) if totals else None
        avg_dur = sum(r["duration"] for r in subset) / len(subset)
        total_cost = _cost(sum(totals)) if totals else "—"
        print(
            f"- **{backend}**: {successes}/{len(subset)} succeeded"
            f"  ·  avg {_fmt(avg_tokens)} tokens/run"
            f"  ·  avg {avg_dur:.0f}s/run"
            f"  ·  total est. {total_cost}"
        )

    total_all = [r["total_tokens"] for r in rows if r["total_tokens"] is not None]
    if total_all:
        grand_total = sum(total_all)
        print(f"\n**Grand total: {_fmt(grand_total)} tokens · est. {_cost(grand_total)}**\n")

    # ── JSON sidecar ──────────────────────────────────────────────────────────
    if ANALYSIS_OUTPUT_JSON:
        sidecar = {
            "model_label": model_label,
            "model": model,
            "cost_per_1m": COST_PER_1M,
            "task": _TASK,
            "rows": rows,
        }
        out_path = Path(ANALYSIS_OUTPUT_JSON)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(sidecar, indent=2))
        print(f"[analysis] sidecar written to {out_path}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
