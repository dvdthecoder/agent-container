"""Unit tests for agent/aider_runner token-capture logic.

aider_runner.py is a script (calls sys.exit at module level), so we import
it by catching SystemExit and then inspect the module object via sys.modules.
"""

from __future__ import annotations

import io
import os
import sys
import types
from unittest.mock import MagicMock, patch


def _make_proc() -> MagicMock:
    proc = MagicMock()
    proc.stdout.__iter__ = lambda self: iter([])
    proc.stderr.__iter__ = lambda self: iter([])
    proc.returncode = 0
    proc.wait.return_value = 0
    return proc


def _load_aider_runner() -> types.ModuleType:
    """Import aider_runner, handling the top-level sys.exit call.

    aider_runner.py calls sys.exit() at module level.  When SystemExit
    propagates through the import machinery Python removes the partially-
    executed module from sys.modules, so a plain `import aider_runner` +
    `except SystemExit` leaves nothing in sys.modules.

    We work around this by using importlib.util to:
    1. Create the module object and register it in sys.modules BEFORE exec.
    2. Execute the module source (catching SystemExit).
    3. Return the already-registered module object directly.
    """
    import importlib.util
    import subprocess

    agent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "agent"))
    runner_path = os.path.join(agent_dir, "aider_runner.py")

    spec = importlib.util.spec_from_file_location("aider_runner", runner_path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    # Register before exec so references survive SystemExit cleanup.
    sys.modules["aider_runner"] = mod

    with (
        patch.object(sys, "argv", ["aider_runner.py", "fix the bug"]),
        patch.object(subprocess, "Popen", return_value=_make_proc()),
        patch.object(subprocess, "run", return_value=MagicMock()),
    ):
        try:
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        except SystemExit:
            pass

    return mod


# ---------------------------------------------------------------------------
# _load_agents_md
# ---------------------------------------------------------------------------


class TestLoadAgentsMd:
    """Tests for the AGENTS.md loader — same logic as opencode_runner."""

    def test_returns_empty_when_file_absent(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AGENT_CONVENTIONS", raising=False)
        mod = _load_aider_runner()
        assert mod._load_agents_md(str(tmp_path)) == ""

    def test_returns_content_when_present(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("## Rules\n- no shell=True\n")
        mod = _load_aider_runner()
        result = mod._load_agents_md(str(tmp_path))
        assert "no shell=True" in result

    def test_blank_file_returns_empty(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("\n\n")
        mod = _load_aider_runner()
        assert mod._load_agents_md(str(tmp_path)) == ""

    def test_truncates_at_cap(self, tmp_path):
        cap = _load_aider_runner()._AGENTS_MD_CAP
        (tmp_path / "AGENTS.md").write_text("a" * (cap + 200))
        mod = _load_aider_runner()
        result = mod._load_agents_md(str(tmp_path))
        assert "truncated" in result
        assert len(result) <= cap + 100

    def test_env_var_used_when_file_absent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_CONVENTIONS", "- no shell=True")
        mod = _load_aider_runner()
        result = mod._load_agents_md(str(tmp_path))
        assert "no shell=True" in result

    def test_file_takes_precedence_over_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_CONVENTIONS", "from env var")
        (tmp_path / "AGENTS.md").write_text("from file")
        mod = _load_aider_runner()
        result = mod._load_agents_md(str(tmp_path))
        assert result == "from file"

    def test_env_var_truncated_when_large(self, tmp_path, monkeypatch):
        mod = _load_aider_runner()
        monkeypatch.setenv("AGENT_CONVENTIONS", "y" * (mod._AGENTS_MD_CAP + 200))
        result = mod._load_agents_md(str(tmp_path))
        assert "truncated" in result


# ---------------------------------------------------------------------------
# Regex tests
# ---------------------------------------------------------------------------


class TestTokenRegex:
    def setup_method(self):
        self.mod = _load_aider_runner()

    def test_matches_standard_aider_line(self):
        line = "Tokens: 2,841 sent, 381 received. Cost: $0.00 message, $0.00 session."
        m = self.mod._TOKEN_RE.search(line)
        assert m is not None
        assert m.group(1) == "2,841"
        assert m.group(2) == "381"

    def test_matches_abbreviated_k_format(self):
        line = "Tokens: 2.7k sent, 109 received. Cost: $0.00 message, $0.00 session."
        m = self.mod._TOKEN_RE.search(line)
        assert m is not None
        assert m.group(1) == "2.7k"
        assert m.group(2) == "109"

    def test_matches_small_numbers(self):
        line = "Tokens: 100 sent, 50 received."
        m = self.mod._TOKEN_RE.search(line)
        assert m is not None
        assert m.group(1) == "100"
        assert m.group(2) == "50"

    def test_does_not_match_unrelated_line(self):
        line = "Applying patch to src/main.py"
        assert self.mod._TOKEN_RE.search(line) is None

    def test_does_not_match_partial_line(self):
        line = "Tokens: 100 sent."
        assert self.mod._TOKEN_RE.search(line) is None


class TestParseTok:
    def setup_method(self):
        self.mod = _load_aider_runner()

    def test_plain_integer(self):
        assert self.mod._parse_tok("381") == 381

    def test_comma_separated(self):
        assert self.mod._parse_tok("2,841") == 2841

    def test_k_suffix(self):
        assert self.mod._parse_tok("2.7k") == 2700

    def test_K_suffix(self):
        assert self.mod._parse_tok("2.7K") == 2700

    def test_m_suffix(self):
        assert self.mod._parse_tok("1.2M") == 1_200_000


# ---------------------------------------------------------------------------
# _stream — accumulates from stderr, forwards all lines to dest
# ---------------------------------------------------------------------------


class TestStream:
    def setup_method(self):
        self.mod = _load_aider_runner()
        # Reset module-level accumulators before each test.
        self.mod._prompt_tokens = 0
        self.mod._completion_tokens = 0

    def _run_stream(self, lines: list[str], is_stderr: bool) -> str:
        dest = io.StringIO()
        source = [line.encode() + b"\n" for line in lines]
        self.mod._stream(iter(source), dest, is_stderr)
        return dest.getvalue()

    def test_stderr_accumulates_tokens(self):
        self._run_stream(
            ["Tokens: 1,000 sent, 200 received. Cost: $0.00 message, $0.00 session."],
            is_stderr=True,
        )
        assert self.mod._prompt_tokens == 1000
        assert self.mod._completion_tokens == 200

    def test_stderr_accumulates_abbreviated_tokens(self):
        self._run_stream(
            ["Tokens: 2.7k sent, 109 received. Cost: $0.00 message, $0.00 session."],
            is_stderr=True,
        )
        assert self.mod._prompt_tokens == 2700
        assert self.mod._completion_tokens == 109

    def test_stderr_accumulates_across_multiple_lines(self):
        lines = [
            "Tokens: 500 sent, 100 received. Cost: $0.00 message, $0.00 session.",
            "some other aider output",
            "Tokens: 300 sent, 80 received. Cost: $0.00 message, $0.00 session.",
        ]
        self._run_stream(lines, is_stderr=True)
        assert self.mod._prompt_tokens == 800
        assert self.mod._completion_tokens == 180

    def test_stdout_also_accumulates_tokens(self):
        # aider version determines which stream gets the Tokens line — scan both.
        self._run_stream(
            ["Tokens: 1,000 sent, 200 received. Cost: $0.00 message, $0.00 session."],
            is_stderr=False,
        )
        assert self.mod._prompt_tokens == 1000
        assert self.mod._completion_tokens == 200

    def test_non_token_lines_forwarded_unchanged(self):
        output = self._run_stream(["hello world", "second line"], is_stderr=False)
        assert "hello world\n" in output
        assert "second line\n" in output

    def test_token_line_still_forwarded_to_dest(self):
        """Token lines should reach dest even while being parsed."""
        output = self._run_stream(
            ["Tokens: 10 sent, 5 received. Cost: $0.00 message, $0.00 session."],
            is_stderr=True,
        )
        assert "Tokens:" in output
