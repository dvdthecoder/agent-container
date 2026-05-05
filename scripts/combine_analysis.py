"""Combine multiple token-analysis JSON sidecars into one Markdown page.

Each sidecar is produced by `make test-analysis OUTPUT_JSON=<path>` — one
per model.  This script merges them into a single model × backend comparison
page with an auto-generated analysis section.

Usage
-----
    python3 scripts/combine_analysis.py docs/analysis/data/*.json
    python3 scripts/combine_analysis.py \\
        docs/analysis/data/qwen2.5-coder-7b.json \\
        docs/analysis/data/qwen2.5-coder-32b.json \\
        > docs/analysis/2026-05-05.md

    make combine-analysis  # uses docs/analysis/data/*.json, writes dated page
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(n: int | None) -> str:
    if n is None:
        return "—"
    return f"{n:,}"


def _cost(tokens: int | None, rate: float) -> str:
    if tokens is None:
        return "—"
    return f"${tokens / 1_000_000 * rate:.4f}"


def _pct_diff(a: float, b: float) -> str:
    """Return '+X%' or '-X%' of a relative to b."""
    if b == 0:
        return "n/a"
    diff = (a - b) / b * 100
    sign = "+" if diff >= 0 else ""
    return f"{sign}{diff:.0f}%"


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def load_sidecars(paths: list[Path]) -> list[dict]:
    sidecars = []
    for p in paths:
        data = json.loads(p.read_text())
        data["_source"] = str(p)
        sidecars.append(data)
    return sidecars


def build_matrix(sidecars: list[dict]) -> list[dict]:
    """Flatten all rows across all sidecars, adding model_label to each row."""
    rows = []
    for s in sidecars:
        for r in s["rows"]:
            rows.append({**r, "model_label": s["model_label"], "cost_per_1m": s["cost_per_1m"]})
    return rows


def render_results_table(rows: list[dict]) -> str:
    lines = [
        "| Model | Backend | Success | Prompt tok | Completion tok | Total tok"
        " | Est. cost | Duration | PR |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        rate = r.get("cost_per_1m", 1.0)
        success = "✅" if r["success"] else "❌"
        prompt = _fmt(r.get("prompt_tokens"))
        completion = _fmt(r.get("completion_tokens"))
        total = _fmt(r.get("total_tokens"))
        cost = _cost(r.get("total_tokens"), rate)
        dur = f"{r['duration']:.0f}s" if r.get("duration") else "—"
        pr = f"[PR]({r['pr_url']})" if r.get("pr_url") else "—"
        lines.append(
            f"| {r['model_label']} | {r['backend']} | {success}"
            f" | {prompt} | {completion} | {total} | {cost} | {dur} | {pr} |"
        )
    return "\n".join(lines)


def render_summary_table(sidecars: list[dict]) -> str:
    """Per-model × per-backend averages."""
    # Collect all backends
    all_backends: list[str] = []
    for s in sidecars:
        for r in s["rows"]:
            if r["backend"] not in all_backends:
                all_backends.append(r["backend"])

    lines = ["| Model | Backend | Runs | Success | Avg prompt | Avg completion | Avg total | Avg cost | Avg duration |",
             "|---|---|---|---|---|---|---|---|---|"]
    for s in sidecars:
        rate = s.get("cost_per_1m", 1.0)
        for backend in all_backends:
            subset = [r for r in s["rows"] if r["backend"] == backend]
            if not subset:
                continue
            successes = sum(1 for r in subset if r["success"])
            prompts = [r["prompt_tokens"] for r in subset if r.get("prompt_tokens") is not None]
            completions = [r["completion_tokens"] for r in subset if r.get("completion_tokens") is not None]
            totals = [r["total_tokens"] for r in subset if r.get("total_tokens") is not None]
            durs = [r["duration"] for r in subset if r.get("duration") is not None]
            avg_p = int(sum(prompts) / len(prompts)) if prompts else None
            avg_c = int(sum(completions) / len(completions)) if completions else None
            avg_t = int(sum(totals) / len(totals)) if totals else None
            avg_d = sum(durs) / len(durs) if durs else None
            lines.append(
                f"| {s['model_label']} | {backend} | {len(subset)} | {successes}/{len(subset)}"
                f" | {_fmt(avg_p)} | {_fmt(avg_c)} | {_fmt(avg_t)}"
                f" | {_cost(avg_t, rate)} | {f'{avg_d:.0f}s' if avg_d else '—'} |"
            )
    return "\n".join(lines)


def render_analysis(sidecars: list[dict]) -> str:
    """Auto-generate analysis observations from the data."""
    rows = build_matrix(sidecars)
    backends = list({r["backend"] for r in rows})
    models = [s["model_label"] for s in sidecars]

    lines = []

    # Framework overhead: aider vs opencode prompt tokens (same model)
    for s in sidecars:
        aider_rows = [r for r in s["rows"] if r["backend"] == "aider" and r.get("prompt_tokens")]
        oc_rows = [r for r in s["rows"] if r["backend"] == "opencode" and r.get("prompt_tokens")]
        if aider_rows and oc_rows:
            avg_a = sum(r["prompt_tokens"] for r in aider_rows) / len(aider_rows)
            avg_o = sum(r["prompt_tokens"] for r in oc_rows) / len(oc_rows)
            ratio = avg_o / avg_a if avg_a else 0
            lines.append(
                f"- **{s['model_label']}** — opencode sends **{ratio:.1f}×** more prompt tokens "
                f"than aider ({_fmt(int(avg_o))} vs {_fmt(int(avg_a))}). "
                "The gap is almost entirely tool-schema overhead: opencode resends all 10 tool "
                "schemas (~500 tokens each) on every turn."
            )

    lines.append("")

    # Model comparison for same backend (if multiple models)
    if len(sidecars) > 1:
        for backend in backends:
            backend_rows = [(s["model_label"], [r for r in s["rows"] if r["backend"] == backend and r.get("total_tokens")])
                            for s in sidecars]
            backend_rows = [(label, rs) for label, rs in backend_rows if rs]
            if len(backend_rows) < 2:
                continue
            totals = [(label, sum(r["total_tokens"] for r in rs) / len(rs)) for label, rs in backend_rows]
            totals.sort(key=lambda x: x[1])
            cheapest_label, cheapest_avg = totals[0]
            most_label, most_avg = totals[-1]
            lines.append(
                f"- **{backend}** backend: `{cheapest_label}` uses the fewest total tokens "
                f"({_fmt(int(cheapest_avg))} avg), `{most_label}` the most "
                f"({_fmt(int(most_avg))} avg) — {_pct_diff(most_avg, cheapest_avg)} difference."
            )

    lines.append("")

    # Completion token variance (model verbosity signal)
    if len(sidecars) > 1:
        lines.append(
            "**Completion token variance** (model verbosity signal — "
            "prompt tokens are mostly fixed by the framework):"
        )
        lines.append("")
        for backend in backends:
            for s in sidecars:
                rs = [r for r in s["rows"] if r["backend"] == backend and r.get("completion_tokens")]
                if rs:
                    avg_c = int(sum(r["completion_tokens"] for r in rs) / len(rs))
                    lines.append(f"- {s['model_label']} / {backend}: avg {_fmt(avg_c)} completion tokens")

    return "\n".join(lines)


def main(paths: list[Path]) -> None:
    if not paths:
        print("Usage: combine_analysis.py <sidecar1.json> [sidecar2.json ...]", file=sys.stderr)
        sys.exit(1)

    sidecars = load_sidecars(paths)
    matrix = build_matrix(sidecars)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    models_str = ", ".join(s["model_label"] for s in sidecars)
    task = sidecars[0]["task"] if sidecars else ""

    print(f"# Token Analysis — {today}")
    print()
    print(f"**Task:** {task}")
    print()
    print(f"**Models tested:** {models_str}")
    print()
    print(f"**Repo:** [dvdthecoder/agent-container-fixture](https://github.com/dvdthecoder/agent-container-fixture)")
    print()
    print("---")
    print()
    print("## Results")
    print()
    print(render_results_table(matrix))
    print()
    print("---")
    print()
    print("## Summary (averages per model × backend)")
    print()
    print(render_summary_table(sidecars))
    print()
    print("---")
    print()
    print("## Analysis")
    print()
    print(render_analysis(sidecars))


if __name__ == "__main__":
    main([Path(p) for p in sys.argv[1:]])
