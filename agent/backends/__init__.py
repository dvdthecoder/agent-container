"""AgentBackend protocol and backend registry.

A backend knows only *what command to run* inside the sandbox container.
Execution (sb.exec) is handled by agent.runner — keeping backends fully
decoupled from the Modal SDK and independently testable.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AgentBackend(Protocol):
    """Structural interface for a coding agent backend."""

    name: str  # "opencode" | "claude" | "gemini" | "stub"
    display_name: str  # human-readable name for UI / logging

    def command(self, task: str) -> list[str]:
        """Return the argv to invoke this agent non-interactively with *task*."""
        ...


def get_backend(name: str) -> AgentBackend:
    """Return the backend for *name*, raising ``ValueError`` if unknown."""
    # Deferred imports keep the individual backend modules independent.
    from agent.backends.claude_code import ClaudeCodeBackend
    from agent.backends.gemini import GeminiBackend
    from agent.backends.opencode import OpenCodeBackend
    from agent.backends.stub import StubBackend

    registry: dict[str, AgentBackend] = {
        "opencode": OpenCodeBackend(),
        "claude": ClaudeCodeBackend(),
        "gemini": GeminiBackend(),
        "stub": StubBackend(),
    }

    if name not in registry:
        raise ValueError(f"Unknown backend: {name!r}. Available: {', '.join(registry)}")
    return registry[name]
