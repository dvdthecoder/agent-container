"""Claude Code CLI backend."""

from __future__ import annotations


class ClaudeCodeBackend:
    """Run Claude Code CLI non-interactively with a task prompt.

    Reads ``ANTHROPIC_API_KEY`` from the environment.
    """

    name = "claude"
    display_name = "Claude Code"

    def command(self, task: str) -> list[str]:
        return ["claude", "--print", task]
