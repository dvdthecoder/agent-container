"""OpenCode backend — default agent for agent-container."""

from __future__ import annotations


class OpenCodeBackend:
    """Run OpenCode non-interactively with a task prompt.

    OpenCode reads ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` / ``OPENCODE_MODEL``
    from the environment — these are injected via Modal secrets.
    """

    name = "opencode"
    display_name = "OpenCode"

    def command(self, task: str) -> list[str]:
        return ["opencode", "--print", "-m", task]
