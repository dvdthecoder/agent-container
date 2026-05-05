"""Structured run log — persists every sandbox event to a local SQLite database.

One row per run in ``runs``, one row per log line/event in ``events``.
The DB lives at ~/.agent-container/runs.db by default and is never sent
anywhere — purely local debugging artefact.

Typical usage
-------------
    logger = RunLogger.create(repo=repo, task=task, backend=backend)
    logger.phase("BOOTING")
    logger.log("preflight", "checking endpoint", level="info")
    logger.finish("success", branch=br, pr_url=url, duration_s=elapsed)
    logger.close()

Reading runs
------------
    store = RunStore()
    for run in store.list_runs(limit=20):
        print(run)
    for event in store.events(run_id):
        print(event)
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Default DB path
# ---------------------------------------------------------------------------

_DEFAULT_DB = Path.home() / ".agent-container" / "runs.db"

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    repo            TEXT NOT NULL,
    task            TEXT NOT NULL,
    backend         TEXT NOT NULL,
    model           TEXT NOT NULL DEFAULT '',
    initiated_by    TEXT NOT NULL DEFAULT 'cli',
    base_branch     TEXT NOT NULL DEFAULT 'main',
    timeout_seconds INTEGER NOT NULL DEFAULT 600,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    outcome         TEXT,
    branch          TEXT,
    pr_url          TEXT,
    duration_s      REAL,
    sandbox_id      TEXT,
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    total_tokens    INTEGER
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL REFERENCES runs(run_id),
    ts         TEXT NOT NULL,
    elapsed_s  REAL NOT NULL,
    phase      TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL,
    level      TEXT NOT NULL DEFAULT 'info',
    message    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
"""


# ---------------------------------------------------------------------------
# Data classes for reading
# ---------------------------------------------------------------------------


@dataclass
class RunRow:
    run_id: str
    repo: str
    task: str
    backend: str
    model: str
    initiated_by: str
    base_branch: str
    timeout_seconds: int
    started_at: str
    finished_at: str | None
    outcome: str | None
    branch: str | None
    pr_url: str | None
    duration_s: float | None
    sandbox_id: str | None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass
class EventRow:
    id: int
    run_id: str
    ts: str
    elapsed_s: float
    phase: str
    source: str
    level: str
    message: str


# ---------------------------------------------------------------------------
# RunLogger — write side
# ---------------------------------------------------------------------------


def new_run_id() -> str:
    """Generate a human-readable run ID with a timestamp prefix."""
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    return f"run-{ts}-{suffix}"


class RunLogger:
    """Write structured events to the SQLite run log."""

    def __init__(self, run_id: str, db_path: Path | None = None) -> None:
        self.run_id = run_id
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.executescript(_DDL)
        self._migrate()
        self._conn.commit()
        self._lock = threading.Lock()
        self._start = time.monotonic()
        self._phase = ""

    def _migrate(self) -> None:
        """Add columns introduced after initial schema (idempotent)."""
        existing = {row[1] for row in self._conn.execute("PRAGMA table_info(runs)").fetchall()}
        migrations = [
            ("model", "TEXT NOT NULL DEFAULT ''"),
            ("initiated_by", "TEXT NOT NULL DEFAULT 'cli'"),
            ("base_branch", "TEXT NOT NULL DEFAULT 'main'"),
            ("timeout_seconds", "INTEGER NOT NULL DEFAULT 600"),
            ("prompt_tokens", "INTEGER"),
            ("completion_tokens", "INTEGER"),
            ("total_tokens", "INTEGER"),
        ]
        for col, defn in migrations:
            if col not in existing:
                self._conn.execute(f"ALTER TABLE runs ADD COLUMN {col} {defn}")  # noqa: S608

    # ------------------------------------------------------------------ write

    @classmethod
    def create(
        cls,
        repo: str,
        task: str,
        backend: str,
        model: str = "",
        db_path: Path | None = None,
        initiated_by: str = "cli",
        base_branch: str = "main",
        timeout_seconds: int = 600,
        run_id: str | None = None,
    ) -> RunLogger:
        """Create a new logger and insert the run row."""
        rid = run_id or new_run_id()
        logger = cls(rid, db_path)
        logger._insert_run(repo, task, backend, model, initiated_by, base_branch, timeout_seconds)
        return logger

    def phase(self, phase: str) -> None:
        """Record a phase transition (BOOTING, CLONING, RUNNING, TESTING, PR)."""
        self._phase = phase
        self.log("runner", f"phase={phase}", level="info", phase=phase)

    def set_sandbox_id(self, sandbox_id: str) -> None:
        """Store the Modal sandbox container ID once it's known."""
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET sandbox_id=? WHERE run_id=?",
                (sandbox_id, self.run_id),
            )
            self._conn.commit()

    def log(
        self,
        source: str,
        message: str,
        level: str = "info",
        phase: str = "",
    ) -> None:
        """Append one event to the log."""
        now = datetime.now(UTC).isoformat()
        elapsed = time.monotonic() - self._start
        ph = phase or self._phase
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (run_id, ts, elapsed_s, phase, source, level, message)"
                " VALUES (?,?,?,?,?,?,?)",
                (self.run_id, now, round(elapsed, 3), ph, source, level, message),
            )
            self._conn.commit()

    def set_token_usage(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
    ) -> None:
        """Persist token consumption for this run (parsed from runner stderr)."""
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET prompt_tokens=?, completion_tokens=?, total_tokens=?"
                " WHERE run_id=?",
                (prompt_tokens, completion_tokens, total_tokens, self.run_id),
            )
            self._conn.commit()

    def finish(
        self,
        outcome: str,
        branch: str | None = None,
        pr_url: str | None = None,
        duration_s: float = 0.0,
    ) -> None:
        """Mark the run as finished with its final outcome."""
        now = datetime.now(UTC).isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET finished_at=?, outcome=?, branch=?, pr_url=?, duration_s=?"
                " WHERE run_id=?",
                (now, outcome, branch, pr_url, round(duration_s, 3), self.run_id),
            )
            self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        try:
            self._conn.close()
        except Exception:  # noqa: S110
            pass

    # ------------------------------------------------------------------ private

    def _insert_run(
        self,
        repo: str,
        task: str,
        backend: str,
        model: str = "",
        initiated_by: str = "cli",
        base_branch: str = "main",
        timeout_seconds: int = 600,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs"  # noqa: S608
                " (run_id, repo, task, backend, model,"
                " initiated_by, base_branch, timeout_seconds, started_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (self.run_id, repo, task, backend, model, initiated_by, base_branch, timeout_seconds, now),
            )
            self._conn.commit()


# ---------------------------------------------------------------------------
# RunStore — read side
# ---------------------------------------------------------------------------


class RunStore:
    """Read run logs from the SQLite database."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB

    def _connect(self) -> sqlite3.Connection:
        if not self._db_path.exists():
            raise FileNotFoundError(f"No run log database found at {self._db_path}")
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def list_runs(self, limit: int = 20) -> list[RunRow]:
        """Return the most recent runs, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [RunRow(**dict(r)) for r in rows]

    def get_run(self, run_id: str) -> RunRow | None:
        """Return a single run by ID, or None if not found."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return RunRow(**dict(row)) if row else None

    def events(
        self,
        run_id: str,
        level: str | None = None,
        phase: str | None = None,
        source: str | None = None,
    ) -> list[EventRow]:
        """Return events for a run, optionally filtered."""
        clauses = ["run_id=?"]
        params: list[object] = [run_id]
        if level:
            clauses.append("level=?")
            params.append(level)
        if phase:
            clauses.append("phase=?")
            params.append(phase)
        if source:
            clauses.append("source=?")
            params.append(source)
        sql = f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY id"  # noqa: S608
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [EventRow(**dict(r)) for r in rows]
