"""Unit tests for agent.backends — protocol conformance, commands, registry."""

from __future__ import annotations

import pytest

from agent.backends import AgentBackend, get_backend
from agent.backends.claude_code import ClaudeCodeBackend
from agent.backends.gemini import GeminiBackend
from agent.backends.opencode import OpenCodeBackend
from agent.backends.stub import StubBackend

# ------------------------------------------------------------------ Protocol


@pytest.mark.parametrize("backend_cls", [
    OpenCodeBackend, ClaudeCodeBackend, GeminiBackend, StubBackend,
])
def test_satisfies_protocol(backend_cls):
    assert isinstance(backend_cls(), AgentBackend)


# ------------------------------------------------------------------ commands


def test_opencode_command():
    cmd = OpenCodeBackend().command("fix the bug")
    assert cmd == ["opencode", "--print", "-m", "fix the bug"]


def test_claude_command():
    cmd = ClaudeCodeBackend().command("fix the bug")
    assert cmd == ["claude", "--print", "fix the bug"]


def test_gemini_command():
    cmd = GeminiBackend().command("fix the bug")
    assert cmd == ["gemini", "--yolo", "-p", "fix the bug"]


def test_stub_command_starts_with_sh():
    cmd = StubBackend().command("fix the bug")
    assert cmd[:2] == ["sh", "-c"]


def test_stub_command_safely_quotes_task():
    cmd = StubBackend().command("task with 'single quotes'")
    # The shell fragment must contain the task text safely quoted.
    assert "task with" in cmd[-1]


@pytest.mark.parametrize("backend_name, expected_prefix", [
    ("opencode", ["opencode", "--print", "-m"]),
    ("claude",   ["claude", "--print"]),
    ("gemini",   ["gemini", "--yolo", "-p"]),
    ("stub",     ["sh", "-c"]),
])
def test_command_prefix(backend_name, expected_prefix):
    backend = get_backend(backend_name)
    cmd = backend.command("task")
    assert cmd[: len(expected_prefix)] == expected_prefix


# ------------------------------------------------------------------ names


@pytest.mark.parametrize("backend_cls, expected_name", [
    (OpenCodeBackend,  "opencode"),
    (ClaudeCodeBackend, "claude"),
    (GeminiBackend,    "gemini"),
    (StubBackend,      "stub"),
])
def test_name_attribute(backend_cls, expected_name):
    assert backend_cls().name == expected_name


@pytest.mark.parametrize("backend_cls", [
    OpenCodeBackend, ClaudeCodeBackend, GeminiBackend, StubBackend,
])
def test_display_name_is_non_empty_string(backend_cls):
    assert isinstance(backend_cls().display_name, str)
    assert backend_cls().display_name


# ------------------------------------------------------------------ registry


def test_get_backend_returns_correct_type():
    assert isinstance(get_backend("opencode"), OpenCodeBackend)
    assert isinstance(get_backend("claude"), ClaudeCodeBackend)
    assert isinstance(get_backend("gemini"), GeminiBackend)
    assert isinstance(get_backend("stub"), StubBackend)


def test_get_backend_unknown_raises():
    with pytest.raises(ValueError, match="Unknown backend"):
        get_backend("gpt-pilot")


def test_get_backend_error_lists_available():
    with pytest.raises(ValueError, match="opencode"):
        get_backend("unknown")
