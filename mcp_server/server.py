"""MCP server exposing agent-container sandbox tools.

Four tools are registered:

  sandbox_run    — boot a Modal sandbox, run an AI agent, return the result
  sandbox_list   — list all runs tracked in the WorkspaceStore
  sandbox_status — get the state + buffered events for a specific run
  sandbox_stop   — cancel a running sandbox

The server integrates with the dashboard WorkspaceStore so tool calls and
dashboard HTTP clients share the same in-memory state when both are running.
When the server is started standalone (python -m mcp_server.server) the store
starts empty; it is NOT pre-populated from a separate dashboard process.

Run modes
---------
stdio (default, for Claude Code / Gemini CLI):
    python -m mcp_server.server

SSE (for remote / multi-client use):
    python -m mcp_server.server --transport sse --port 8001
"""

from __future__ import annotations

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastmcp import FastMCP

from dashboard.store import WorkspaceStore
from sandbox.config import SandboxConfig
from sandbox.sandbox import ModalSandbox
from sandbox.spec import AgentTaskSpec

mcp = FastMCP(
    "agent-container",
    instructions=(
        "Tools to run AI coding agents in ephemeral Modal sandboxes. "
        "Use sandbox_run to kick off a task, sandbox_status to poll progress, "
        "sandbox_list to see all runs, and sandbox_stop to cancel."
    ),
)

# Shared state — also importable by the dashboard so they can share a store.
store = WorkspaceStore()
_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="mcp-sandbox")
_futures: dict[str, Any] = {}


# ------------------------------------------------------------------ tools


@mcp.tool()
async def sandbox_run(
    task: str,
    repo: str,
    base_branch: str = "main",
    backend: str = "opencode",
    create_pr: bool = True,
    run_tests: bool = True,
    timeout_seconds: int = 300,
    image: str | None = None,
) -> dict:
    """Boot an ephemeral Modal sandbox, run an AI coding agent on the task, and return the result.

    Args:
        task: Plain-English description of what the agent should do.
        repo: Full GitHub or GitLab URL (https:// or git@).
        base_branch: Branch to clone and base any PR on. Defaults to "main".
        backend: Agent backend — "opencode" (default), "claude", "gemini", or "stub".
        create_pr: Whether to open a pull/merge request when the agent produces a diff.
        run_tests: Whether to auto-detect and run the project test suite after the agent.
        timeout_seconds: Hard sandbox timeout. Defaults to 300 s.
        image: Optional Docker image override; defaults to the base agent image.

    Returns:
        AgentTaskResult as a dict with keys: success, run_id, branch, pr_url,
        diff_stat, tests, duration_seconds, error, backend.
    """
    run_id = uuid.uuid4().hex[:12]
    store.create_run(run_id=run_id, repo=repo, task=task, backend=backend)

    def _on_event(event_type: str, payload: dict) -> None:
        store.push_event(run_id, event_type, payload)

    def _run() -> dict:
        spec_kwargs: dict[str, Any] = dict(
            repo=repo,
            task=task,
            base_branch=base_branch,
            backend=backend,
            create_pr=create_pr,
            run_tests=run_tests,
            timeout_seconds=timeout_seconds,
        )
        if image:
            spec_kwargs["image"] = image

        spec = AgentTaskSpec(**spec_kwargs)
        config = SandboxConfig()
        result = ModalSandbox(config).run(spec, on_event=_on_event)
        # Omit full diff from MCP response — it can be very large.
        # Use our generated run_id (not Modal's sandbox object_id).
        d = result.to_dict()
        d.pop("diff", None)
        d.pop("run_id", None)
        return d

    # Run blocking sandbox code in thread pool so the MCP event loop stays free.
    future = _executor.submit(_run)
    _futures[run_id] = future
    result_dict = await asyncio.get_event_loop().run_in_executor(None, future.result)
    return {"run_id": run_id, **result_dict}


@mcp.tool()
async def sandbox_list() -> list[dict]:
    """List all agent runs — active, completed, and failed.

    Returns a summary list (no event log) sorted newest-first by start time.
    """
    runs = store.list_runs()
    runs.sort(key=lambda r: r.started_at, reverse=True)
    return [r.to_dict() for r in runs]


@mcp.tool()
async def sandbox_status(run_id: str) -> dict:
    """Get the current state and buffered event log for a specific run.

    Args:
        run_id: The run ID returned by sandbox_run.

    Returns:
        Run state dict plus an "events" list with all buffered lifecycle events.
    """
    state = store.get_run(run_id)
    if state is None:
        return {"error": f"run {run_id!r} not found"}
    d = state.to_dict()
    # Include safe subset of events (no embedded AgentTaskResult objects).
    d["events"] = [
        {k: v for k, v in ev.items() if k != "result"}
        for ev in store.events_from(run_id, 0)
    ]
    return d


@mcp.tool()
async def sandbox_stop(run_id: str) -> dict:
    """Cancel a running sandbox and mark it as failed.

    Args:
        run_id: The run ID to stop.

    Returns:
        {"run_id": ..., "cancelled": true} or an error dict.
    """
    state = store.get_run(run_id)
    if state is None:
        return {"error": f"run {run_id!r} not found"}

    future = _futures.get(run_id)
    if future is not None:
        future.cancel()

    store.push_event(run_id, "done", {"success": False, "error": "cancelled by sandbox_stop"})
    return {"run_id": run_id, "cancelled": True}


# ------------------------------------------------------------------ entry point


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="agent-container MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport mode (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8001,
        help="Port for SSE transport (default: 8001)",
    )
    args = parser.parse_args()

    if args.transport == "sse":
        mcp.run(transport="sse", host="0.0.0.0", port=args.port)  # noqa: S104
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
