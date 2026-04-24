"""Unit tests for dashboard.store — WorkspaceStore and RunState."""

from __future__ import annotations

import time

from dashboard.store import WorkspaceStore

# ------------------------------------------------------------------ helpers


def _store() -> WorkspaceStore:
    return WorkspaceStore()


def _create(store: WorkspaceStore, run_id: str = "run-001") -> None:
    store.create_run(
        run_id=run_id, repo="https://github.com/org/repo", task="fix it", backend="opencode"
    )


# ------------------------------------------------------------------ create / get / list


def test_create_and_get_run():
    s = _store()
    _create(s)
    state = s.get_run("run-001")
    assert state is not None
    assert state.run_id == "run-001"
    assert state.phase == "BOOTING"
    assert state.events == []


def test_get_missing_run_returns_none():
    s = _store()
    assert s.get_run("nope") is None


def test_list_runs_empty():
    assert _store().list_runs() == []


def test_list_runs_returns_all():
    s = _store()
    _create(s, "r1")
    _create(s, "r2")
    ids = {r.run_id for r in s.list_runs()}
    assert ids == {"r1", "r2"}


# ------------------------------------------------------------------ push_event / phase transitions


def test_push_phase_event_updates_phase():
    s = _store()
    _create(s)
    s.push_event("run-001", "phase", {"phase": "CLONING"})
    assert s.get_run("run-001").phase == "CLONING"


def test_push_done_success_sets_done():
    s = _store()
    _create(s)
    s.push_event("run-001", "done", {"success": True})
    assert s.get_run("run-001").phase == "DONE"


def test_push_done_failure_sets_failed():
    s = _store()
    _create(s)
    s.push_event("run-001", "done", {"success": False})
    assert s.get_run("run-001").phase == "FAILED"


def test_push_log_event_appended():
    s = _store()
    _create(s)
    s.push_event("run-001", "log", {"text": "hello"})
    events = s.events_from("run-001", 0)
    assert len(events) == 1
    assert events[0]["type"] == "log"
    assert events[0]["text"] == "hello"


def test_push_to_missing_run_is_noop():
    s = _store()
    s.push_event("ghost", "log", {"text": "irrelevant"})  # should not raise


def test_event_has_ts_field():
    s = _store()
    _create(s)
    before = time.time()
    s.push_event("run-001", "log", {"text": "x"})
    after = time.time()
    ts = s.events_from("run-001", 0)[0]["ts"]
    assert before <= ts <= after


# ------------------------------------------------------------------ events_from cursor


def test_events_from_cursor_zero_returns_all():
    s = _store()
    _create(s)
    for i in range(5):
        s.push_event("run-001", "log", {"text": str(i)})
    assert len(s.events_from("run-001", 0)) == 5


def test_events_from_cursor_advances():
    s = _store()
    _create(s)
    for i in range(4):
        s.push_event("run-001", "log", {"text": str(i)})
    batch1 = s.events_from("run-001", 0)
    batch2 = s.events_from("run-001", len(batch1))
    assert batch2 == []
    # Add more
    s.push_event("run-001", "log", {"text": "new"})
    batch3 = s.events_from("run-001", len(batch1))
    assert len(batch3) == 1
    assert batch3[0]["text"] == "new"


def test_events_from_missing_run_returns_empty():
    assert _store().events_from("ghost", 0) == []


# ------------------------------------------------------------------ is_terminal


def test_is_terminal_booting_false():
    s = _store()
    _create(s)
    assert s.is_terminal("run-001") is False


def test_is_terminal_done_true():
    s = _store()
    _create(s)
    s.push_event("run-001", "done", {"success": True})
    assert s.is_terminal("run-001") is True


def test_is_terminal_failed_true():
    s = _store()
    _create(s)
    s.push_event("run-001", "done", {"success": False})
    assert s.is_terminal("run-001") is True


def test_is_terminal_missing_run_true():
    """Missing runs are treated as terminal so SSE clients disconnect."""
    assert _store().is_terminal("ghost") is True


# ------------------------------------------------------------------ to_dict


def test_to_dict_has_expected_keys():
    s = _store()
    _create(s)
    d = s.get_run("run-001").to_dict()
    assert d["run_id"] == "run-001"
    assert d["repo"] == "https://github.com/org/repo"
    assert d["task"] == "fix it"
    assert d["backend"] == "opencode"
    assert d["phase"] == "BOOTING"
    assert "started_at" in d
    assert d["result"] is None
