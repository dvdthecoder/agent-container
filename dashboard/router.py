"""FastAPI router for the agent-container dashboard.

Routes
------
GET  /runs              — list all runs from SQLite (CLI + dashboard, newest first)
POST /runs              — start a new run (returns run_id immediately)
GET  /runs/{id}         — get a single run (SQLite metadata + live phase if active)
DELETE /runs/{id}        — cancel a queued/running run (best-effort)
GET  /runs/{id}/stream  — SSE stream of lifecycle events (dashboard runs only)
GET  /runs/{id}/events  — past log events from SQLite (for replay on page load)
GET  /tokens            — token usage per run, sorted by total_tokens desc
GET  /serve/status      — model server status (deployed / idle / unknown)
POST /serve/deploy      — trigger a modal deploy for the given profile
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.log_store import RunRow, RunStore, new_run_id
from dashboard.store import WorkspaceStore
from sandbox.config import SandboxConfig
from sandbox.sandbox import ModalSandbox
from sandbox.spec import AgentTaskSpec

router = APIRouter()

# Module-level singletons shared between router functions and injected into tests.
store = WorkspaceStore()
run_store = RunStore()
_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="sandbox")
_futures: dict[str, Future] = {}  # run_id → Future for cancellation


# ------------------------------------------------------------------ request models


class StartRunRequest(BaseModel):
    repo: str
    task: str
    backend: str = "opencode"
    base_branch: str = "main"
    create_pr: bool = True
    run_tests: bool = True
    timeout_seconds: int = 300


class DeployRequest(BaseModel):
    profile: str = "test"
    model: str | None = None  # only used for prod profile


# ------------------------------------------------------------------ helpers


def _run_row_to_dict(row: RunRow, active_phase: str | None = None) -> dict[str, Any]:
    return {
        "run_id": row.run_id,
        "repo": row.repo,
        "task": row.task,
        "backend": row.backend,
        "initiated_by": row.initiated_by,
        "base_branch": row.base_branch,
        "timeout_seconds": row.timeout_seconds,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "outcome": row.outcome,
        "branch": row.branch,
        "pr_url": row.pr_url,
        "duration_s": row.duration_s,
        "sandbox_id": row.sandbox_id,
        # live phase from WorkspaceStore while run is active.
        # outcome=None with no active_phase means the run was interrupted (CLI
        # killed, container crash) — show UNKNOWN rather than RUNNING.
        "phase": active_phase
        or ("DONE" if row.outcome == "success" else ("FAILED" if row.outcome else "UNKNOWN")),
    }


# ------------------------------------------------------------------ /runs routes


@router.get("/runs")
def list_runs() -> list[dict[str, Any]]:
    """Return all runs newest-first from SQLite (CLI + dashboard)."""
    try:
        rows = run_store.list_runs(limit=100)
    except FileNotFoundError:
        return []
    result = []
    for row in rows:
        ws = store.get_run(row.run_id)
        active_phase = ws.phase if ws and ws.phase not in ("DONE", "FAILED") else None
        result.append(_run_row_to_dict(row, active_phase))
    return result


@router.post("/runs", status_code=202)
def start_run(body: StartRunRequest) -> dict[str, str]:
    run_id = new_run_id()

    # Register in WorkspaceStore so SSE works before SQLite row is committed.
    store.create_run(
        run_id=run_id,
        repo=body.repo,
        task=body.task,
        backend=body.backend,
    )

    def _run() -> None:
        def on_event(event_type: str, payload: dict) -> None:
            store.push_event(run_id, event_type, payload)

        spec = AgentTaskSpec(
            repo=body.repo,
            task=body.task,
            backend=body.backend,
            base_branch=body.base_branch,
            create_pr=body.create_pr,
            run_tests=body.run_tests,
            timeout_seconds=body.timeout_seconds,
            initiated_by="dashboard",
            run_id=run_id,
        )
        config = SandboxConfig.from_env()
        ModalSandbox(config).run(spec, on_event=on_event)

    future = _executor.submit(_run)
    _futures[run_id] = future
    return {"run_id": run_id}


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    # Try SQLite first (covers CLI + completed dashboard runs).
    try:
        row = run_store.get_run(run_id)
    except FileNotFoundError:
        row = None

    if row is not None:
        ws = store.get_run(run_id)
        active_phase = ws.phase if ws else None
        return _run_row_to_dict(row, active_phase)

    # Fall back to WorkspaceStore for runs not yet flushed to SQLite.
    ws = store.get_run(run_id)
    if ws is not None:
        return ws.to_dict()

    raise HTTPException(status_code=404, detail="run not found")


@router.delete("/runs/{run_id}", status_code=204)
def cancel_run(run_id: str) -> None:
    future = _futures.get(run_id)
    if future is not None:
        future.cancel()
    ws = store.get_run(run_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="run not found")
    store.push_event(run_id, "done", {"success": False, "error": "cancelled"})


@router.get("/runs/{run_id}/events")
def run_events(run_id: str) -> list[dict[str, Any]]:
    """Return past log events from SQLite for replay on page load."""
    try:
        rows = run_store.events(run_id)
    except FileNotFoundError:
        return []
    return [
        {
            "type": "log",
            "ts": row.ts,
            "elapsed_s": row.elapsed_s,
            "phase": row.phase,
            "source": row.source,
            "level": row.level,
            "text": row.message,
        }
        for row in rows
        if row.source.startswith("sandbox:")  # only agent output lines
    ]


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str) -> StreamingResponse:
    ws = store.get_run(run_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="run not found")

    async def _generate():
        cursor = 0
        while True:
            events = store.events_from(run_id, cursor)
            for event in events:
                safe = {k: v for k, v in event.items() if k != "result"}
                yield f"data: {json.dumps(safe)}\n\n"
            cursor += len(events)

            if store.is_terminal(run_id) and not store.events_from(run_id, cursor):
                break

            await asyncio.sleep(0.2)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ------------------------------------------------------------------ /tokens route


@router.get("/tokens")
def list_tokens(backend: str = "", date_from: str = "", date_to: str = "") -> list[dict[str, Any]]:
    """Return per-run token usage sorted by total_tokens descending.

    Optional query params:
      backend   — filter by backend name (e.g. "opencode")
      date_from — ISO date string lower bound on started_at (inclusive)
      date_to   — ISO date string upper bound on started_at (inclusive)
    """
    try:
        rows = run_store.list_runs(limit=500)
    except FileNotFoundError:
        return []

    result = []
    for row in rows:
        # Skip runs with no token data.
        if row.total_tokens is None:
            continue
        if backend and row.backend != backend:
            continue
        if date_from and row.started_at < date_from:
            continue
        if date_to and row.started_at > date_to + "T23:59:59":
            continue
        result.append(
            {
                "run_id": row.run_id,
                "repo": row.repo,
                "task": row.task,
                "backend": row.backend,
                "started_at": row.started_at,
                "outcome": row.outcome,
                "prompt_tokens": row.prompt_tokens or 0,
                "completion_tokens": row.completion_tokens or 0,
                "total_tokens": row.total_tokens or 0,
                "duration_s": row.duration_s,
            }
        )

    # Sort by total_tokens descending — heaviest runs first.
    result.sort(key=lambda r: r["total_tokens"], reverse=True)
    return result


# ------------------------------------------------------------------ /serve routes


@router.get("/serve/status")
def serve_status() -> dict[str, Any]:
    """Return the deployment status of the model server."""
    try:
        result = subprocess.run(
            ["modal", "app", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return {"status": "unknown", "error": result.stderr.strip()}
        apps = json.loads(result.stdout)
        serve_apps = [a for a in apps if a.get("name", "").startswith("agent-container-serve")]
        return {"status": "ok", "apps": serve_apps}
    except Exception as exc:
        return {"status": "unknown", "error": str(exc)}


@router.post("/serve/deploy", status_code=202)
def serve_deploy(body: DeployRequest) -> dict[str, str]:
    """Trigger a background modal deploy for the requested profile."""
    env: dict[str, str] = {"SERVE_PROFILE": body.profile}
    if body.model:
        env["SERVE_MODEL"] = body.model

    import os

    full_env = {**os.environ, **env}

    def _deploy() -> None:
        subprocess.run(
            ["modal", "deploy", "modal/serve.py"],
            env=full_env,
            capture_output=True,
        )

    _executor.submit(_deploy)
    return {"status": "deploying", "profile": body.profile}
