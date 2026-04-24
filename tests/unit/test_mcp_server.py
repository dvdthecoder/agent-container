"""Unit tests for mcp_server.server — all Modal + sandbox calls mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import mcp_server.server as srv
from dashboard.store import WorkspaceStore
from sandbox.result import AgentTaskResult

# ------------------------------------------------------------------ fixtures


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch):
    """Give each test an isolated WorkspaceStore."""
    s = WorkspaceStore()
    monkeypatch.setattr(srv, "store", s)
    monkeypatch.setattr(srv, "_futures", {})
    return s


def _ok_result(run_id: str = "abc123") -> AgentTaskResult:
    return AgentTaskResult(
        success=True,
        run_id=run_id,
        branch="agent/opencode-20260424-120000",
        pr_url="https://github.com/org/repo/pull/7",
        diff="diff --git a/f b/f\n+fix",
        diff_stat="1 file changed",
        backend="opencode",
        duration_seconds=12.3,
    )


def _fail_result(run_id: str = "abc123") -> AgentTaskResult:
    return AgentTaskResult(
        success=False,
        run_id=run_id,
        error="agent crashed",
        backend="opencode",
        duration_seconds=3.1,
    )


# ------------------------------------------------------------------ sandbox_run


@pytest.mark.asyncio
async def test_sandbox_run_returns_result_dict(fresh_store):
    result = _ok_result()

    with patch("mcp_server.server.ModalSandbox") as MockSandbox:
        MockSandbox.return_value.run.return_value = result
        out = await srv.sandbox_run(task="fix it", repo="https://github.com/org/repo")

    assert out["success"] is True
    assert out["pr_url"] == "https://github.com/org/repo/pull/7"
    assert out["diff_stat"] == "1 file changed"
    # Full diff is stripped from MCP response (can be very large)
    assert "diff" not in out


@pytest.mark.asyncio
async def test_sandbox_run_omits_diff_from_response(fresh_store):
    result = _ok_result()

    with patch("mcp_server.server.ModalSandbox") as MockSandbox:
        MockSandbox.return_value.run.return_value = result
        out = await srv.sandbox_run(task="task", repo="https://github.com/org/repo")

    assert "diff" not in out


@pytest.mark.asyncio
async def test_sandbox_run_records_run_in_store(fresh_store):
    result = _ok_result()

    with patch("mcp_server.server.ModalSandbox") as MockSandbox:
        MockSandbox.return_value.run.return_value = result
        out = await srv.sandbox_run(task="fix it", repo="https://github.com/org/repo")

    run_id = out["run_id"]
    assert fresh_store.get_run(run_id) is not None


@pytest.mark.asyncio
async def test_sandbox_run_failure_returns_success_false(fresh_store):
    result = _fail_result()

    with patch("mcp_server.server.ModalSandbox") as MockSandbox:
        MockSandbox.return_value.run.return_value = result
        out = await srv.sandbox_run(task="fix it", repo="https://github.com/org/repo")

    assert out["success"] is False
    assert out["error"] == "agent crashed"


@pytest.mark.asyncio
async def test_sandbox_run_forwards_backend_to_spec(fresh_store):
    result = _ok_result()
    captured = {}

    def fake_run(spec, on_event=None):
        captured["backend"] = spec.backend
        return result

    with patch("mcp_server.server.ModalSandbox") as MockSandbox:
        MockSandbox.return_value.run.side_effect = fake_run
        await srv.sandbox_run(task="t", repo="https://github.com/org/r", backend="claude")

    assert captured["backend"] == "claude"


@pytest.mark.asyncio
async def test_sandbox_run_on_event_pushes_to_store(fresh_store):
    """on_event callback emitted by ModalSandbox.run reaches the store."""

    def fake_run(spec, on_event=None):
        if on_event:
            on_event("phase", {"phase": "RUNNING"})
        return _ok_result()

    with patch("mcp_server.server.ModalSandbox") as MockSandbox:
        MockSandbox.return_value.run.side_effect = fake_run
        out = await srv.sandbox_run(task="t", repo="https://github.com/org/r")

    run_id = out["run_id"]
    events = fresh_store.events_from(run_id, 0)
    phase_events = [e for e in events if e["type"] == "phase"]
    assert any(e["phase"] == "RUNNING" for e in phase_events)


# ------------------------------------------------------------------ sandbox_list


@pytest.mark.asyncio
async def test_sandbox_list_empty(fresh_store):
    result = await srv.sandbox_list()
    assert result == []


@pytest.mark.asyncio
async def test_sandbox_list_returns_runs(fresh_store):
    fresh_store.create_run("r1", "https://github.com/o/a", "task A", "opencode")
    fresh_store.create_run("r2", "https://github.com/o/b", "task B", "claude")

    result = await srv.sandbox_list()
    ids = {r["run_id"] for r in result}
    assert ids == {"r1", "r2"}


@pytest.mark.asyncio
async def test_sandbox_list_sorted_newest_first(fresh_store):
    import time

    fresh_store.create_run("r1", "https://github.com/o/a", "A", "opencode")
    time.sleep(0.01)
    fresh_store.create_run("r2", "https://github.com/o/b", "B", "opencode")

    result = await srv.sandbox_list()
    assert result[0]["run_id"] == "r2"


# ------------------------------------------------------------------ sandbox_status


@pytest.mark.asyncio
async def test_sandbox_status_missing_run(fresh_store):
    result = await srv.sandbox_status("ghost")
    assert "error" in result


@pytest.mark.asyncio
async def test_sandbox_status_returns_state_and_events(fresh_store):
    fresh_store.create_run("r1", "https://github.com/o/a", "task", "opencode")
    fresh_store.push_event("r1", "phase", {"phase": "RUNNING"})
    fresh_store.push_event("r1", "log", {"text": "agent output"})

    result = await srv.sandbox_status("r1")

    assert result["run_id"] == "r1"
    assert result["phase"] == "RUNNING"
    assert len(result["events"]) == 2
    assert result["events"][0]["type"] == "phase"


@pytest.mark.asyncio
async def test_sandbox_status_events_exclude_result_key(fresh_store):
    """The embedded AgentTaskResult object must not leak into the status response."""
    fresh_store.create_run("r1", "https://github.com/o/a", "t", "opencode")
    fresh_store.push_event("r1", "done", {"success": True, "result": _ok_result()})

    result = await srv.sandbox_status("r1")
    for event in result["events"]:
        assert "result" not in event


# ------------------------------------------------------------------ sandbox_stop


@pytest.mark.asyncio
async def test_sandbox_stop_missing_run(fresh_store):
    result = await srv.sandbox_stop("ghost")
    assert "error" in result


@pytest.mark.asyncio
async def test_sandbox_stop_marks_run_terminal(fresh_store):
    fresh_store.create_run("r1", "https://github.com/o/a", "task", "opencode")

    result = await srv.sandbox_stop("r1")

    assert result["cancelled"] is True
    assert fresh_store.is_terminal("r1") is True


@pytest.mark.asyncio
async def test_sandbox_stop_cancels_future(fresh_store):
    fresh_store.create_run("r1", "https://github.com/o/a", "task", "opencode")
    mock_future = MagicMock()
    srv._futures["r1"] = mock_future

    await srv.sandbox_stop("r1")

    mock_future.cancel.assert_called_once()
