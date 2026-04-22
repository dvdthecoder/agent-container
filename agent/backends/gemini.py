"""Gemini CLI backend."""

from __future__ import annotations


class GeminiBackend:
    """Run Gemini CLI non-interactively with a task prompt.

    Reads ``GEMINI_API_KEY`` from the environment.
    ``--yolo`` suppresses interactive confirmations.
    """

    name = "gemini"
    display_name = "Gemini CLI"

    def command(self, task: str) -> list[str]:
        return ["gemini", "--yolo", "-p", task]
