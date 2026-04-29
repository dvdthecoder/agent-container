"""aider backend — direct Chat Completions, no proxy needed."""

from __future__ import annotations


class AiderBackend:
    """Run aider non-interactively with a task prompt.

    aider calls ``/v1/chat/completions`` directly — no Responses API proxy.
    Reads ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` / ``OPENCODE_MODEL`` from env.

    Uses text-based diff editing: the model returns structured diffs, aider
    applies them.  No function calling required at the inference level.
    """

    name = "aider"
    display_name = "aider"

    def command(self, task: str) -> list[str]:
        return ["python3", "/aider_runner.py", task]
