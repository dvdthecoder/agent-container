"""Unit tests for AgentTaskSpec — no external services required."""

from pathlib import Path

import pytest

from sandbox.spec import AgentTaskSpec, _expand_task_spec


class TestAgentTaskSpecValidation:
    def test_raises_when_task_and_task_file_both_none(self):
        with pytest.raises(ValueError, match="both are None"):
            AgentTaskSpec(repo="https://github.com/org/repo")

    def test_raises_when_both_task_and_task_file_provided(self, tmp_path):
        task_file = tmp_path / "task.md"
        task_file.write_text("do something")

        with pytest.raises(ValueError, match="not both"):
            AgentTaskSpec(
                repo="https://github.com/org/repo",
                task="do something",
                task_file=task_file,
            )

    def test_raises_when_task_file_not_found(self, tmp_path):
        missing = tmp_path / "nonexistent.md"

        with pytest.raises(ValueError, match="task_file not found"):
            AgentTaskSpec(
                repo="https://github.com/org/repo",
                task_file=missing,
            )

    def test_raises_when_repo_url_invalid(self):
        with pytest.raises(ValueError, match="full URL"):
            AgentTaskSpec(repo="org/repo", task="fix it")

    def test_raises_when_timeout_agent_is_zero(self):
        with pytest.raises(ValueError, match="timeout_agent"):
            AgentTaskSpec(
                repo="https://github.com/org/repo",
                task="fix it",
                timeout_agent=0,
            )

    def test_raises_when_timeout_agent_negative(self):
        with pytest.raises(ValueError, match="timeout_agent"):
            AgentTaskSpec(
                repo="https://github.com/org/repo",
                task="fix it",
                timeout_agent=-1,
            )

    def test_raises_when_timeout_coldstart_is_zero(self):
        with pytest.raises(ValueError, match="timeout_coldstart"):
            AgentTaskSpec(
                repo="https://github.com/org/repo",
                task="fix it",
                timeout_coldstart=0,
            )

    def test_raises_when_timeout_tests_negative(self):
        with pytest.raises(ValueError, match="timeout_tests"):
            AgentTaskSpec(
                repo="https://github.com/org/repo",
                task="fix it",
                timeout_tests=-5,
            )


class TestAgentTaskSpecDefaults:
    def test_default_values(self):
        spec = AgentTaskSpec(repo="https://github.com/org/repo", task="fix it")

        assert spec.base_branch == "main"
        assert spec.image is None
        assert spec.env == {}
        assert spec.timeout_coldstart == 300
        assert spec.timeout_agent == 600
        assert spec.timeout_tests == 120
        assert spec.create_pr is True
        assert spec.backend == "opencode"

    def test_accepts_git_ssh_url(self):
        spec = AgentTaskSpec(repo="git@github.com:org/repo.git", task="fix it")
        assert spec.repo == "git@github.com:org/repo.git"

    def test_task_file_coerced_to_path(self, tmp_path):
        task_file = tmp_path / "task.md"
        task_file.write_text("fix the bug")

        spec = AgentTaskSpec(repo="https://github.com/org/repo", task_file=str(task_file))

        assert isinstance(spec.task_file, Path)


class TestAgentTaskSpecTimeouts:
    def test_timeout_seconds_sets_timeout_agent(self):
        spec = AgentTaskSpec(
            repo="https://github.com/org/repo",
            task="fix it",
            timeout_seconds=900,
        )
        assert spec.timeout_agent == 900

    def test_total_timeout_sums_phases(self):
        spec = AgentTaskSpec(
            repo="https://github.com/org/repo",
            task="fix it",
            timeout_coldstart=120,
            timeout_agent=300,
            timeout_tests=60,
        )
        assert spec.total_timeout == 480


class TestAgentTaskSpecResolvers:
    def test_resolved_task_returns_task_string(self):
        spec = AgentTaskSpec(repo="https://github.com/org/repo", task="fix the login bug")
        assert spec.resolved_task() == "fix the login bug"

    def test_resolved_task_reads_from_file(self, tmp_path):
        task_file = tmp_path / "task.md"
        task_file.write_text("  fix the login bug  \n")

        spec = AgentTaskSpec(repo="https://github.com/org/repo", task_file=task_file)

        assert spec.resolved_task() == "fix the login bug"

    def test_resolved_image_uses_override(self):
        spec = AgentTaskSpec(
            repo="https://github.com/org/repo",
            task="fix it",
            image="python:3.11-slim",
        )
        assert spec.resolved_image("ubuntu:22.04") == "python:3.11-slim"

    def test_resolved_image_falls_back_to_default(self):
        spec = AgentTaskSpec(repo="https://github.com/org/repo", task="fix it")
        assert spec.resolved_image("ubuntu:22.04") == "ubuntu:22.04"

    def test_resolved_prompt_plain_string_unchanged(self):
        spec = AgentTaskSpec(repo="https://github.com/org/repo", task="fix the login bug")
        assert spec.resolved_prompt() == "fix the login bug"

    def test_resolved_prompt_expands_yaml_task_spec(self):
        yaml_task = "task: fix the off-by-one\nacceptance_criteria: pytest test.py passes"
        spec = AgentTaskSpec(repo="https://github.com/org/repo", task=yaml_task)
        prompt = spec.resolved_prompt()
        assert "## Task" in prompt
        assert "fix the off-by-one" in prompt
        assert "## Acceptance Criteria" in prompt
        assert "pytest test.py passes" in prompt

    def test_resolved_task_returns_raw_for_yaml_spec(self):
        """resolved_task() returns the raw YAML string — not the expanded prompt."""
        yaml_task = "task: fix the off-by-one\nacceptance_criteria: pytest test.py passes"
        spec = AgentTaskSpec(repo="https://github.com/org/repo", task=yaml_task)
        assert spec.resolved_task() == yaml_task


class TestExpandTaskSpec:
    def test_plain_string_returned_unchanged(self):
        assert _expand_task_spec("fix the bug") == "fix the bug"

    def test_task_only(self):
        result = _expand_task_spec("task: fix the off-by-one bug")
        assert result == "## Task\nfix the off-by-one bug"

    def test_task_with_acceptance_criteria(self):
        raw = "task: fix it\nacceptance_criteria: pytest passes"
        result = _expand_task_spec(raw)
        assert "## Task\nfix it" in result
        assert "## Acceptance Criteria\npytest passes" in result

    def test_task_with_constraints_list(self):
        raw = "task: fix it\nconstraints:\n  - modify only mathlib.py\n  - no new deps"
        result = _expand_task_spec(raw)
        assert "## Constraints" in result
        assert "- modify only mathlib.py" in result
        assert "- no new deps" in result

    def test_task_with_constraints_string(self):
        raw = "task: fix it\nconstraints: modify only mathlib.py"
        result = _expand_task_spec(raw)
        assert "## Constraints\nmodify only mathlib.py" in result

    def test_task_with_context_files_list(self):
        raw = "task: fix it\ncontext_files:\n  - mathlib.py\n  - test_mathlib.py"
        result = _expand_task_spec(raw)
        assert "## Relevant Files" in result
        assert "- mathlib.py" in result
        assert "- test_mathlib.py" in result

    def test_task_with_all_fields(self):
        raw = (
            "task: fix the off-by-one in sum_to_n\n"
            "acceptance_criteria: pytest test_mathlib.py -q passes\n"
            "constraints:\n  - modify only mathlib.py\n"
            "context_files:\n  - mathlib.py\n"
        )
        result = _expand_task_spec(raw)
        assert "## Task" in result
        assert "## Acceptance Criteria" in result
        assert "## Constraints" in result
        assert "## Relevant Files" in result

    def test_invalid_yaml_returned_unchanged(self):
        raw = "task: [\nunclosed bracket"
        assert _expand_task_spec(raw) == raw

    def test_yaml_without_task_key_returned_unchanged(self):
        raw = "description: fix it\nsome_other_key: value"
        assert _expand_task_spec(raw) == raw

    def test_non_dict_yaml_returned_unchanged(self):
        raw = "- item one\n- item two"
        assert _expand_task_spec(raw) == raw
