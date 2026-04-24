"""FastAPI router for the agent-container dashboard.

Routes
------
GET  /runs              — list all runs (summary, no events)
POST /runs              — start a new run in a background thread
GET  /runs/{id}         — get a single run (summary)
DELETE /runs/{id}        — cancel a queued/running run (best-effort)
GET  /runs/{id}/stream  — SSE stream of lifecycle events
"""

from __future__ import annotations

import asyncio
import json
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from dashboard.store import WorkspaceStore
from sandbox.config import SandboxConfig
from sandbox.sandbox import ModalSandbox
from sandbox.spec import AgentTaskSpec

router = APIRouter()

# Module-level singletons shared between router functions and injected into tests.
store = WorkspaceStore()
_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="sandbox")
_futures: dict[str, Future] = {}  # run_id → Future for cancellation


# ------------------------------------------------------------------ request model


class StartRunRequest(BaseModel):
    repo: str
    task: str
    backend: str = "opencode"
    base_branch: str = "main"
    create_pr: bool = True
    run_tests: bool = True
    timeout_seconds: int = 300


# ------------------------------------------------------------------ routes


@router.get("/runs")
def list_runs() -> list[dict[str, Any]]:
    return [r.to_dict() for r in store.list_runs()]


@router.post("/runs", status_code=202)
def start_run(body: StartRunRequest) -> dict[str, str]:
    run_id = uuid.uuid4().hex[:12]

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
        )
        config = SandboxConfig()
        ModalSandbox(config).run(spec, on_event=on_event)

    future = _executor.submit(_run)
    _futures[run_id] = future
    return {"run_id": run_id}


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    state = store.get_run(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="run not found")
    return state.to_dict()


@router.delete("/runs/{run_id}", status_code=204)
def cancel_run(run_id: str) -> None:
    future = _futures.get(run_id)
    if future is not None:
        future.cancel()  # no-op if already running, but worth trying
    state = store.get_run(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="run not found")
    # Mark terminal so SSE clients disconnect.
    store.push_event(run_id, "done", {"success": False, "error": "cancelled"})


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str) -> StreamingResponse:
    state = store.get_run(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="run not found")

    async def _generate():
        cursor = 0
        while True:
            events = store.events_from(run_id, cursor)
            for event in events:
                # Filter out the result object (not JSON-serialisable as-is).
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
