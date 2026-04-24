"""Example 3 — programmatic MCP client using fastmcp.

Shows how to call the agent-container MCP tools from Python code — useful for
testing the server locally or building higher-level automation on top of it.

The MCP server must be running before this script is executed:
    make mcp   (or: python3 -m mcp_server.server --transport sse --port 8001)

Usage (SSE transport):
    python3 examples/mcp_client.py

To test with stdio transport directly from code, import the FastMCP app
object and call the tools as regular async functions instead:

    from mcp_server.server import sandbox_run, sandbox_list
    import asyncio
    result = asyncio.run(sandbox_run(task="...", repo="..."))
"""

from __future__ import annotations

import asyncio
import json


async def demo_via_direct_import() -> None:
    """Call the MCP tool functions directly — no transport layer needed.

    This is the fastest way to test logic without a running server.
    """
    # Import the tool functions directly — they are plain async functions.
    from mcp_server.server import sandbox_list, sandbox_run, sandbox_status, sandbox_stop

    print("=== sandbox_list (empty) ===")
    runs = await sandbox_list()
    print(json.dumps(runs, indent=2))

    print("\n=== sandbox_run (stub backend — no LLM cost) ===")
    result = await sandbox_run(
        task="Fix the off-by-one bug in sum_to_n() — use range(1, n + 1).",
        repo="https://github.com/dvdthecoder/agent-container-fixture",
        backend="stub",
        create_pr=False,
        run_tests=False,
        timeout_seconds=120,
    )
    print(json.dumps(result, indent=2, default=str))

    run_id = result["run_id"]

    print(f"\n=== sandbox_status({run_id}) ===")
    status = await sandbox_status(run_id)
    # Show phase + first 3 events only to keep output short.
    print(f"phase  : {status['phase']}")
    print(f"events : {len(status['events'])} total")
    for ev in status["events"][:3]:
        print(f"  {ev['type']:8}  {ev.get('phase') or ev.get('text', '')[:60]}")

    print(f"\n=== sandbox_list (after run) ===")
    runs = await sandbox_list()
    print(f"{len(runs)} run(s) recorded")

    print(f"\n=== sandbox_stop({run_id}) — already terminal, noop ===")
    stop = await sandbox_stop(run_id)
    print(json.dumps(stop, indent=2))


if __name__ == "__main__":
    asyncio.run(demo_via_direct_import())
