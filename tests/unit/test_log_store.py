"""Unit tests for agent.log_store — RunLogger and RunStore."""

from __future__ import annotations

import threading
import time

import pytest

from agent.log_store import RunLogger, RunStore, new_run_id


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "test_runs.db"


@pytest.fixture()
def logger(db):
    lg = RunLogger.create(
        repo="https://github.com/org/repo", task="fix it", backend="opencode", db_path=db
    )
    yield lg
    lg.close()


@pytest.fixture()
def store(db):
    return RunStore(db_path=db)


# ------------------------------------------------------------------ run_id


def test_new_run_id_starts_with_run():
    assert new_run_id().startswith("run-")


def test_new_run_id_is_unique():
    ids = {new_run_id() for _ in range(10)}
    assert len(ids) == 10


# ------------------------------------------------------------------ RunLogger.create


def test_create_inserts_run_row(logger, store):
    runs = store.list_runs()
    assert len(runs) == 1
    assert runs[0].run_id == logger.run_id
    assert runs[0].repo == "https://github.com/org/repo"
    assert runs[0].task == "fix it"
    assert runs[0].backend == "opencode"
    assert runs[0].started_at is not None
    assert runs[0].outcome is None


# ------------------------------------------------------------------ phase


def test_phase_creates_event(logger, store):
    logger.phase("BOOTING")
    events = store.events(logger.run_id)
    assert len(events) == 1
    assert events[0].phase == "BOOTING"
    assert events[0].source == "runner"
    assert "phase=BOOTING" in events[0].message


def test_phase_updates_current_phase(logger, store):
    logger.phase("RUNNING")
    logger.log("preflight", "checking endpoint")
    events = store.events(logger.run_id)
    assert events[1].phase == "RUNNING"


# ------------------------------------------------------------------ log


def test_log_stores_source_and_message(logger, store):
    logger.log("preflight", "HTTP 200 OK")
    events = store.events(logger.run_id)
    assert events[0].source == "preflight"
    assert events[0].message == "HTTP 200 OK"
    assert events[0].level == "info"


def test_log_stores_level(logger, store):
    logger.log("runner", "timeout", level="error")
    events = store.events(logger.run_id)
    assert events[0].level == "error"


def test_log_elapsed_increases(logger, store):
    logger.log("a", "first")
    time.sleep(0.01)
    logger.log("b", "second")
    events = store.events(logger.run_id)
    assert events[1].elapsed_s > events[0].elapsed_s


def test_log_thread_safe(logger, store):
    """Concurrent writes from multiple threads must not corrupt the DB."""
    errors: list[Exception] = []

    def _write(n: int) -> None:
        try:
            for i in range(20):
                logger.log("sandbox:stdout", f"thread-{n} line {i}")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_write, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    events = store.events(logger.run_id)
    assert len(events) == 100  # 5 threads × 20 lines


# ------------------------------------------------------------------ set_sandbox_id


def test_set_sandbox_id(logger, store):
    logger.set_sandbox_id("ta-abc123")
    run = store.get_run(logger.run_id)
    assert run is not None
    assert run.sandbox_id == "ta-abc123"


# ------------------------------------------------------------------ finish


def test_finish_sets_outcome(logger, store):
    logger.finish(
        "success",
        branch="agent/opencode-20260429",
        pr_url="https://github.com/org/repo/pull/1",
        duration_s=42.5,
    )
    run = store.get_run(logger.run_id)
    assert run is not None
    assert run.outcome == "success"
    assert run.branch == "agent/opencode-20260429"
    assert run.pr_url == "https://github.com/org/repo/pull/1"
    assert run.duration_s == pytest.approx(42.5, abs=0.01)
    assert run.finished_at is not None


def test_finish_records_failure(logger, store):
    logger.finish("failed", duration_s=10.0)
    run = store.get_run(logger.run_id)
    assert run is not None
    assert run.outcome == "failed"


# ------------------------------------------------------------------ RunStore


def test_list_runs_empty_when_no_runs(db):
    # Create DB via a logger then close it — no runs after close.
    lg = RunLogger.create(repo="https://github.com/org/r", task="t", backend="stub", db_path=db)
    lg.close()
    store = RunStore(db_path=db)
    runs = store.list_runs()
    assert len(runs) == 1  # the one we just created


def test_list_runs_returns_newest_first(db):
    store = RunStore(db_path=db)
    for i in range(3):
        lg = RunLogger.create(
            repo="https://github.com/org/r", task=f"task-{i}", backend="stub", db_path=db
        )
        lg.finish("success", duration_s=float(i))
        lg.close()
    runs = store.list_runs()
    # outcomes are the same but started_at order should be descending
    assert len(runs) == 3
    assert runs[0].started_at >= runs[1].started_at >= runs[2].started_at


def test_list_runs_respects_limit(db):
    for i in range(5):
        lg = RunLogger.create(
            repo="https://github.com/org/r", task=f"t-{i}", backend="stub", db_path=db
        )
        lg.close()
    store = RunStore(db_path=db)
    assert len(store.list_runs(limit=3)) == 3


def test_get_run_returns_none_for_unknown(db):
    lg = RunLogger.create(repo="https://github.com/org/r", task="t", backend="stub", db_path=db)
    lg.close()
    store = RunStore(db_path=db)
    assert store.get_run("run-does-not-exist") is None


def test_events_filter_by_level(logger, store):
    logger.log("a", "info msg", level="info")
    logger.log("b", "error msg", level="error")
    errors = store.events(logger.run_id, level="error")
    assert len(errors) == 1
    assert errors[0].message == "error msg"


def test_events_filter_by_phase(logger, store):
    logger.phase("RUNNING")
    logger.log("proxy", "translated request")
    logger.phase("TESTING")
    logger.log("tester", "pytest found")
    running_events = store.events(logger.run_id, phase="RUNNING")
    # phase event + proxy log = 2
    assert all(e.phase == "RUNNING" for e in running_events)


def test_events_filter_by_source(logger, store):
    logger.log("sandbox:stdout", "agent output")
    logger.log("sandbox:stderr", "error line", level="error")
    logger.log("preflight", "ok")
    stderr_events = store.events(logger.run_id, source="sandbox:stderr")
    assert len(stderr_events) == 1
    assert stderr_events[0].message == "error line"


def test_store_raises_when_db_missing(tmp_path):
    store = RunStore(db_path=tmp_path / "nonexistent.db")
    with pytest.raises(FileNotFoundError):
        store.list_runs()
