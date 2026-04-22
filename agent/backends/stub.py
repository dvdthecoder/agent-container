"""Stub backend — echoes the task, makes no code changes.

Used in integration tests to validate the full sandbox lifecycle
(create → clone → run → diff → PR → destroy) without spending money on a
real model call.
"""

from __future__ import annotations

import shlex


class StubBackend:
    name = "stub"
    display_name = "Stub (testing only)"

    def command(self, task: str) -> list[str]:
        # Echo the task to stdout so callers can verify the agent ran.
        return ["sh", "-c", f"echo {shlex.quote(task)}"]
