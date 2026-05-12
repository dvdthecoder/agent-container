"""Unit tests for sandbox.diff_scanner."""

from __future__ import annotations

import textwrap  # used for _CLEAN_DIFF only

from sandbox.diff_scanner import ScanResult, Violation, _parse_diff, scan_diff

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CLEAN_DIFF = textwrap.dedent("""\
    diff --git a/mathlib.py b/mathlib.py
    index 0000001..0000002 100644
    --- a/mathlib.py
    +++ b/mathlib.py
    @@ -1,3 +1,4 @@
     def add(a, b):
    -    return a + b
    +    return a + b  # fixed
    +
    +def subtract(a, b):
    +    return a - b
""")


def _make_diff(filename: str, added_lines: list[str]) -> str:
    rows = [
        f"diff --git a/{filename} b/{filename}",
        "index 0000001..0000002 100644",
        f"--- a/{filename}",
        f"+++ b/{filename}",
        f"@@ -1,1 +1,{1 + len(added_lines)} @@",
        " placeholder",
    ]
    rows.extend(f"+{line}" for line in added_lines)
    return "\n".join(rows) + "\n"


# ---------------------------------------------------------------------------
# _parse_diff
# ---------------------------------------------------------------------------


class TestParseDiff:
    def test_returns_empty_for_empty_string(self):
        assert _parse_diff("") == []

    def test_extracts_filename(self):
        result = _parse_diff(_CLEAN_DIFF)
        assert len(result) == 1
        assert result[0][0] == "mathlib.py"

    def test_extracts_added_lines_only(self):
        result = _parse_diff(_CLEAN_DIFF)
        _, lines = result[0]
        contents = [line for _, line in lines]
        assert "    return a + b  # fixed" in contents
        assert "def subtract(a, b):" in contents
        # Context lines (space prefix) and removed lines (-) must not appear
        assert "    return a + b" not in contents

    def test_line_numbers_are_sequential(self):
        diff = _make_diff("foo.py", ["line one", "line two", "line three"])
        result = _parse_diff(diff)
        _, lines = result[0]
        nums = [n for n, _ in lines]
        assert nums == [1, 2, 3]

    def test_multiple_files(self):
        diff = _make_diff("a.py", ["x = 1"]) + _make_diff("b.py", ["y = 2"])
        result = _parse_diff(diff)
        assert len(result) == 2
        fnames = [f for f, _ in result]
        assert "a.py" in fnames
        assert "b.py" in fnames

    def test_strips_plus_b_prefix_from_filename(self):
        diff = "+++ b/src/lib/deep.py\n+added line\n"
        result = _parse_diff(diff)
        assert result[0][0] == "src/lib/deep.py"


# ---------------------------------------------------------------------------
# scan_diff — clean diff
# ---------------------------------------------------------------------------


class TestScanDiffClean:
    def test_clean_diff_passes(self):
        result = scan_diff(_CLEAN_DIFF)
        assert result.passed is True
        assert result.violations == []

    def test_empty_diff_passes(self):
        result = scan_diff("")
        assert result.passed is True

    def test_passed_false_only_on_errors(self):
        # Warnings alone must not flip passed to False
        diff = _make_diff("other.py", ["x = 1"])
        result = scan_diff(diff, context_files=["mathlib.py"])
        assert result.passed is True
        assert len(result.warnings) == 1


# ---------------------------------------------------------------------------
# scan_diff — secret detection
# ---------------------------------------------------------------------------


class TestSecretDetection:
    def test_aws_access_key(self):
        # Construct fake key at runtime — avoids GitHub push-protection scanning
        # the literal.  Pattern: AKIA + 16 chars of [0-9A-Z].
        fake_key = "AKI" + "A" + "FAKEKEY1234567890"
        diff = _make_diff("config.py", [f"key = '{fake_key}'"])
        result = scan_diff(diff)
        assert result.passed is False
        assert any(v.rule == "aws-access-key" for v in result.errors)

    def test_github_pat(self):
        diff = _make_diff("deploy.py", ["token = 'ghp_" + "a" * 36 + "'"])
        result = scan_diff(diff)
        assert result.passed is False
        assert any(v.rule == "github-pat" for v in result.errors)

    def test_github_fine_grained_pat(self):
        diff = _make_diff("ci.py", ["t = 'github_pat_" + "x" * 59 + "'"])
        result = scan_diff(diff)
        assert result.passed is False
        assert any(v.rule == "github-fine-grained-pat" for v in result.errors)

    def test_openai_api_key(self):
        diff = _make_diff("llm.py", ["key = 'sk-" + "Z" * 48 + "'"])
        result = scan_diff(diff)
        assert result.passed is False
        assert any(v.rule == "openai-api-key" for v in result.errors)

    def test_slack_bot_token(self):
        # Build fake token at runtime — avoids committing a literal that matches
        # GitHub push-protection patterns.  xoxb- prefix + 24+ [A-Za-z0-9-] chars.
        fake_tok = "xox" + "b-FAKE-UNIT-TEST-SLACK-BOT-TOKEN-NOTREAL"
        diff = _make_diff("notify.py", [f"tok = '{fake_tok}'"])
        result = scan_diff(diff)
        assert result.passed is False
        assert any(v.rule == "slack-bot-token" for v in result.errors)

    def test_hardcoded_password(self):
        diff = _make_diff("db.py", ['password = "supersecret123"'])
        result = scan_diff(diff)
        assert result.passed is False
        assert any(v.rule == "hardcoded-credential" for v in result.errors)

    def test_hardcoded_api_key(self):
        diff = _make_diff("client.py", ["api_key = 'my-secret-key-value'"])
        result = scan_diff(diff)
        assert result.passed is False
        assert any(v.rule == "hardcoded-credential" for v in result.errors)

    def test_violation_carries_correct_metadata(self):
        diff = _make_diff("secrets.py", ["password = 'hunter2_long_enough'"])
        result = scan_diff(diff)
        v = result.errors[0]
        assert v.file == "secrets.py"
        assert v.line_num == 1
        assert "hunter2" in v.line
        assert v.severity == "error"

    def test_multiple_secrets_all_reported(self):
        fake_key = "AKI" + "A" + "FAKEKEY1234567890"
        fake_slack = "xox" + "b-FAKE-UNIT-TEST-SLACK-BOT-TOKEN-NOTREAL"
        diff = _make_diff(
            "bad.py",
            [
                f"key = '{fake_key}'",
                f"token = '{fake_slack}'",
            ],
        )
        result = scan_diff(diff)
        assert len(result.errors) == 2


# ---------------------------------------------------------------------------
# scan_diff — scope violations
# ---------------------------------------------------------------------------


class TestScopeViolations:
    def test_in_scope_file_no_warning(self):
        diff = _make_diff("mathlib.py", ["x = 1"])
        result = scan_diff(diff, context_files=["mathlib.py"])
        scope_warnings = [v for v in result.warnings if v.rule == "scope-violation"]
        assert scope_warnings == []

    def test_out_of_scope_file_warns(self):
        diff = _make_diff("other.py", ["x = 1"])
        result = scan_diff(diff, context_files=["mathlib.py"])
        scope_warnings = [v for v in result.warnings if v.rule == "scope-violation"]
        assert len(scope_warnings) == 1
        assert scope_warnings[0].severity == "warning"
        assert "other.py" in scope_warnings[0].detail

    def test_basename_matching(self):
        # diff path is src/lib/mathlib.py but context_files says mathlib.py
        diff = _make_diff("src/lib/mathlib.py", ["x = 1"])
        result = scan_diff(diff, context_files=["mathlib.py"])
        scope_warnings = [v for v in result.warnings if v.rule == "scope-violation"]
        assert scope_warnings == []

    def test_no_context_files_skips_scope_check(self):
        diff = _make_diff("anything.py", ["x = 1"])
        result = scan_diff(diff, context_files=None)
        scope_warnings = [v for v in result.warnings if v.rule == "scope-violation"]
        assert scope_warnings == []

    def test_scope_warning_does_not_fail_scan(self):
        diff = _make_diff("unexpected.py", ["x = 1"])
        result = scan_diff(diff, context_files=["expected.py"])
        assert result.passed is True

    def test_scope_line_num_is_zero(self):
        diff = _make_diff("wrong.py", ["x = 1"])
        result = scan_diff(diff, context_files=["right.py"])
        v = next(v for v in result.violations if v.rule == "scope-violation")
        assert v.line_num == 0


# ---------------------------------------------------------------------------
# scan_diff — OWASP patterns
# ---------------------------------------------------------------------------


class TestOwaspPatterns:
    def test_eval_injection(self):
        diff = _make_diff("runner.py", ["result = eval(user_input)"])
        result = scan_diff(diff)
        assert any(v.rule == "owasp-eval-injection" for v in result.warnings)

    def test_shell_true(self):
        diff = _make_diff("deploy.py", ["subprocess.run(cmd, shell=True)"])
        result = scan_diff(diff)
        assert any(v.rule == "owasp-shell-true" for v in result.warnings)

    def test_os_system(self):
        diff = _make_diff("util.py", ["os.system('rm -rf /tmp/work')"])
        result = scan_diff(diff)
        assert any(v.rule == "owasp-os-system" for v in result.warnings)

    def test_pickle_loads(self):
        diff = _make_diff("cache.py", ["obj = pickle.loads(data)"])
        result = scan_diff(diff)
        assert any(v.rule == "owasp-pickle-deserialisation" for v in result.warnings)

    def test_pickle_load(self):
        diff = _make_diff("cache.py", ["obj = pickle.load(f)"])
        result = scan_diff(diff)
        assert any(v.rule == "owasp-pickle-deserialisation" for v in result.warnings)

    def test_yaml_load_unsafe(self):
        diff = _make_diff("config.py", ["data = yaml.load(stream)"])
        result = scan_diff(diff)
        assert any(v.rule == "owasp-yaml-load-unsafe" for v in result.warnings)

    def test_yaml_load_with_loader_is_safe(self):
        diff = _make_diff("config.py", ["data = yaml.load(stream, Loader=yaml.SafeLoader)"])
        result = scan_diff(diff)
        assert not any(v.rule == "owasp-yaml-load-unsafe" for v in result.warnings)

    def test_owasp_warning_does_not_fail_scan(self):
        diff = _make_diff("script.py", ["eval(user_code)"])
        result = scan_diff(diff)
        assert result.passed is True

    def test_shell_true_in_comment_not_flagged(self):
        # Comment contains shell=True — should NOT be flagged (# on same line)
        diff = _make_diff("run.py", ["subprocess.run(cmd)  # shell=True would be unsafe"])
        result = scan_diff(diff)
        assert not any(v.rule == "owasp-shell-true" for v in result.warnings)


# ---------------------------------------------------------------------------
# ScanResult helpers
# ---------------------------------------------------------------------------


class TestScanResult:
    def test_errors_property(self):
        r = ScanResult(
            passed=False,
            violations=[
                Violation("error", "rule-a", "f.py", 1, "", ""),
                Violation("warning", "rule-b", "f.py", 2, "", ""),
            ],
        )
        assert len(r.errors) == 1
        assert r.errors[0].rule == "rule-a"

    def test_warnings_property(self):
        r = ScanResult(
            passed=True,
            violations=[
                Violation("warning", "rule-b", "f.py", 2, "", ""),
            ],
        )
        assert len(r.warnings) == 1

    def test_violation_str(self):
        v = Violation("error", "aws-access-key", "config.py", 5, "  key = 'AKIA...'", "details")
        s = str(v)
        assert "[error]" in s
        assert "aws-access-key" in s
        assert "config.py:5" in s
