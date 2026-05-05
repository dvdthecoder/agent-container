"""Orchestrate a full model × backend analysis matrix.

For each model in MATRIX_MODELS:
  1. Deploy an isolated Modal app (SERVE_ISOLATED=1)
  2. Wait until the endpoint is ready
  3. Run token_analysis.py against that endpoint, write a JSON sidecar

Then combine all sidecars into a dated Markdown page.

Usage
-----
    python3 scripts/run_matrix.py
    python3 scripts/run_matrix.py --backends aider        # aider only
    python3 scripts/run_matrix.py --date 2026-05-05
    make analysis-matrix

Environment (read from .env):
    OPENAI_BASE_URL   required — used to extract the Modal org slug so
                      per-model isolated endpoint URLs can be derived
    OPENCODE_MODEL    required — the model name to set for each run
                      (overridden per-model by the script)
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Bootstrap — add project root to path and load .env
_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(_root / ".env")

# ---------------------------------------------------------------------------
# Matrix definition — edit here to add / remove models
# ---------------------------------------------------------------------------

MATRIX_MODELS = [
    {
        "key":   "qwen2.5-coder-32b",
        "label": "Qwen2.5-Coder 32B · A100 80GB",
        "gpu":   "A100 80GB",
    },
    {
        "key":   "qwen3-30b",
        "label": "Qwen3 30B-A3B · A100 80GB",
        "gpu":   "A100 80GB",
    },
]

SIDECAR_DIR = _root / "docs" / "analysis" / "data"
ANALYSIS_DIR = _root / "docs" / "analysis"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _app_name(model_key: str) -> str:
    """Return the isolated Modal app name for a model key."""
    slug = model_key.replace(".", "-")
    return f"agent-container-serve-{slug}"


def _endpoint_url(app_name: str, base_url: str) -> str:
    """Derive the Modal web endpoint URL for *app_name* from *base_url*.

    Replicates the logic in wait_for_serve.py so the matrix runner is
    self-contained.
    """
    m = re.match(r"(https://[^-]+)--[^.]+\.modal\.run", base_url)
    if not m:
        raise ValueError(
            f"Cannot parse org slug from OPENAI_BASE_URL={base_url!r}. "
            "Ensure OPENAI_BASE_URL is set to the deployed Modal endpoint."
        )
    org_prefix = m.group(1)
    return f"{org_prefix}--{app_name}-serve.modal.run"


def _run(cmd: list[str], env: dict | None = None, check: bool = True) -> int:
    merged = {**os.environ, **(env or {})}
    print(f"\n$ {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, env=merged)  # noqa: S603
    if check and result.returncode != 0:
        print(f"[matrix] command failed (exit {result.returncode}): {' '.join(cmd)}", flush=True)
        sys.exit(result.returncode)
    return result.returncode


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def deploy_model(model: dict, wait_timeout: float) -> None:
    print(f"\n{'='*60}", flush=True)
    print(f"[matrix] deploying {model['label']} ...", flush=True)
    print(f"{'='*60}", flush=True)

    app = _app_name(model["key"])
    _run(
        ["modal", "deploy", "modal/serve.py"],
        env={
            "SERVE_MODEL": model["key"],
            "SERVE_PROFILE": "prod",
        },
    )
    _run(
        [sys.executable, "scripts/wait_for_serve.py",
         "--app-name", app,
         "--timeout", str(wait_timeout)],
    )


def run_analysis(model: dict, base_url: str, backends: str, runs: int,
                 cost_per_1m: float) -> Path:
    app = _app_name(model["key"])
    endpoint = _endpoint_url(app, base_url)
    sidecar = SIDECAR_DIR / f"{model['key']}.json"

    print(f"\n[matrix] running analysis for {model['label']} ...", flush=True)
    print(f"[matrix] endpoint: {endpoint}", flush=True)

    _run(
        [sys.executable, "scripts/token_analysis.py"],
        env={
            "OPENAI_BASE_URL": endpoint,
            "OPENCODE_MODEL": model["key"],
            "ANALYSIS_BACKENDS": backends,
            "ANALYSIS_RUNS": str(runs),
            "ANALYSIS_COST_PER_1M": str(cost_per_1m),
            "ANALYSIS_NO_PR": "0",
            "ANALYSIS_MODEL_LABEL": model["label"],
            "ANALYSIS_ENDPOINT": endpoint,
            "ANALYSIS_OUTPUT_JSON": str(sidecar),
        },
    )
    return sidecar


def combine(date: str) -> Path:
    sidecars = sorted(SIDECAR_DIR.glob("*.json"))
    if not sidecars:
        print("[matrix] no sidecars found — skipping combine", flush=True)
        sys.exit(1)

    out = ANALYSIS_DIR / f"{date}.md"
    print(f"\n[matrix] combining {len(sidecars)} sidecars → {out}", flush=True)

    with out.open("w") as fh:
        result = subprocess.run(  # noqa: S603
            [sys.executable, "scripts/combine_analysis.py", *[str(s) for s in sidecars]],
            capture_output=False,
            stdout=fh,
        )
    if result.returncode != 0:
        sys.exit(result.returncode)
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full model × backend analysis matrix.")
    parser.add_argument("--backends", default="aider,opencode",
                        help="Comma-separated backends (default: aider,opencode)")
    parser.add_argument("--runs", type=int, default=1,
                        help="Runs per backend per model (default: 1)")
    parser.add_argument("--cost-per-1m", type=float, default=1.00,
                        help="USD per 1M tokens for cost estimate (default: 1.00)")
    parser.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                        help="Date string for the output filename (default: today)")
    parser.add_argument("--wait-timeout", type=float, default=900.0,
                        help="Seconds to wait for each endpoint after deploy (default: 900)")
    parser.add_argument("--skip-deploy", action="store_true",
                        help="Skip modal deploy steps (use already-running isolated apps)")
    parser.add_argument("--models", default="",
                        help="Comma-separated model keys to run (default: all). "
                             "e.g. --models qwen3-8b,qwen3-30b")
    args = parser.parse_args()

    base_url = os.environ.get("OPENAI_BASE_URL", "")
    if not base_url:
        print("[matrix] ERROR: OPENAI_BASE_URL is not set. Set it in .env.", flush=True)
        sys.exit(1)

    SIDECAR_DIR.mkdir(parents=True, exist_ok=True)

    models = MATRIX_MODELS
    if args.models:
        keys = {k.strip() for k in args.models.split(",")}
        models = [m for m in MATRIX_MODELS if m["key"] in keys]
        if not models:
            print(f"[matrix] ERROR: no models matched --models={args.models!r}. "
                  f"Available: {[m['key'] for m in MATRIX_MODELS]}", flush=True)
            sys.exit(1)

    total = len(models)
    for i, model in enumerate(models, 1):
        print(f"\n[matrix] model {i}/{total}: {model['label']}", flush=True)

        if not args.skip_deploy:
            deploy_model(model, args.wait_timeout)

        run_analysis(
            model,
            base_url=base_url,
            backends=args.backends,
            runs=args.runs,
            cost_per_1m=args.cost_per_1m,
        )


    out = combine(args.date)
    print(f"\n[matrix] done — {out}", flush=True)


if __name__ == "__main__":
    main()
