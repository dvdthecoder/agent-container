"""Unit tests for AgentTaskResult and SuiteResult — no external services required."""

import json

from sandbox.result import AgentTaskResult, SuiteResult


class TestSuiteResult:
    def test_success_true_when_no_failures(self):
        result = SuiteResult(passed=24, failed=0, output="24 passed in 3.2s")
        assert result.success is True

    def test_success_false_when_failures(self):
        result = SuiteResult(passed=22, failed=2, output="2 failed, 22 passed")
        assert result.success is False

    def test_success_false_when_all_fail(self):
        result = SuiteResult(passed=0, failed=5, output="5 failed")
        assert result.success is False

    def test_to_dict_structure(self):
        result = SuiteResult(passed=10, failed=1, output="output", runner_name="pytest")
        d = result.to_dict()

        assert d == {
            "passed": 10,
            "failed": 1,
            "output": "output",
            "runner_name": "pytest",
        }

    def test_to_dict_runner_name_none(self):
        result = SuiteResult(passed=5, failed=0, output="ok")
        assert result.to_dict()["runner_name"] is None


class TestAgentTaskResult:
    def _make_result(self, **kwargs) -> AgentTaskResult:
        defaults = {"success": True, "run_id": "run-abc-123"}
        return AgentTaskResult(**{**defaults, **kwargs})

    def test_to_dict_minimal(self):
        result = self._make_result()
        d = result.to_dict()

        assert d["success"] is True
        assert d["run_id"] == "run-abc-123"
        assert d["tests"] is None
        assert d["pr_url"] is None
        assert d["branch"] is None

    def test_to_dict_with_test_result(self):
        tests = SuiteResult(passed=24, failed=0, output="24 passed", runner_name="pytest")
        result = self._make_result(tests=tests)

        d = result.to_dict()

        assert d["tests"]["passed"] == 24
        assert d["tests"]["failed"] == 0
        assert d["tests"]["runner_name"] == "pytest"

    def test_to_dict_with_all_fields(self):
        tests = SuiteResult(passed=5, failed=0, output="5 passed")
        result = AgentTaskResult(
            success=True,
            run_id="run-xyz",
            branch="agent/fix-login-20260409",
            pr_url="https://github.com/org/repo/pull/42",
            diff="--- a/auth.py\n+++ b/auth.py",
            diff_stat="+12 −3",
            tests=tests,
            duration_seconds=134.5,
            backend="claude",
        )
        d = result.to_dict()

        assert d["branch"] == "agent/fix-login-20260409"
        assert d["pr_url"] == "https://github.com/org/repo/pull/42"
        assert d["diff_stat"] == "+12 −3"
        assert d["duration_seconds"] == 134.5
        assert d["backend"] == "claude"

    def test_to_json_is_valid_json(self):
        result = self._make_result()
        parsed = json.loads(result.to_json())
        assert parsed["run_id"] == "run-abc-123"

    def test_to_json_round_trips(self):
        tests = SuiteResult(passed=3, failed=0, output="ok", runner_name="go test")
        result = AgentTaskResult(
            success=True,
            run_id="run-round-trip",
            branch="agent/test",
            pr_url="https://github.com/org/repo/pull/1",
            diff="diff content",
            diff_stat="+5 −2",
            tests=tests,
            duration_seconds=42.0,
            error=None,
            backend="opencode",
        )

        parsed = json.loads(result.to_json())

        assert parsed == result.to_dict()

    def test_failed_result_has_error(self):
        result = self._make_result(success=False, error="test suite failed: 3 failures")
        d = result.to_dict()

        assert d["success"] is False
        assert d["error"] == "test suite failed: 3 failures"

    def test_default_backend_is_opencode(self):
        result = self._make_result()
        assert result.backend == "opencode"
