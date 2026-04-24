"""In-memory run store for the dashboard.

WorkspaceStore is a thread-safe registry that accumulates events emitted by
ModalSandbox.run() via the on_event callback.  The SSE router polls it with
cursor-based reads so no asyncio queues or call_soon_threadsafe are needed.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from sandbox.result import AgentTaskResult

# Phases in lifecycle order.  The store auto-advances phase on well-known events.
PHASES = ["BOOTING", "CLONING", "RUNNING", "TESTING", "PR", "DONE", "FAILED"]


@dataclass
class RunState:
    """All state for a single agent run."""

    run_id: str
    repo: str
    task: str
    backend: str
    phase: str = "BOOTING"
    started_at: float = field(default_factory=time.time)
    events: list[dict[str, Any]] = field(default_factory=list)
    result: AgentTaskResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "repo": self.repo,
            "task": self.task,
            "backend": self.backend,
            "phase": self.phase,
            "started_at": self.started_at,
            "result": self.result.to_dict() if self.result else None,
        }


class WorkspaceStore:
    """Thread-safe store for all active and completed runs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, RunState] = {}

    # ------------------------------------------------------------------ write

    def create_run(self, run_id: str, repo: str, task: str, backend: str) -> RunState:
        state = RunState(run_id=run_id, repo=repo, task=task, backend=backend)
        with self._lock:
            self._runs[run_id] = state
        return state

    def push_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        """Append an event and update phase if the event carries one."""
        event = {"type": event_type, "ts": time.time(), **payload}
        with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return
            state.events.append(event)
            # Advance phase automatically for well-known phase events.
            if event_type == "phase" and payload.get("phase") in PHASES:
                state.phase = payload["phase"]
            elif event_type == "done":
                state.phase = "DONE" if payload.get("success") else "FAILED"
                # Persist result summary on the state for the /runs list.
                if "result" in payload:
                    state.result = payload["result"]

    # ------------------------------------------------------------------ read

    def get_run(self, run_id: str) -> RunState | None:
        with self._lock:
            return self._runs.get(run_id)

    def list_runs(self) -> list[RunState]:
        with self._lock:
            return list(self._runs.values())

    def events_from(self, run_id: str, cursor: int) -> list[dict[str, Any]]:
        """Return events at index >= cursor.  Safe to call from any thread."""
        with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return []
            return state.events[cursor:]

    def is_terminal(self, run_id: str) -> bool:
        with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return True
            return state.phase in ("DONE", "FAILED")
