"""Unit tests for AgentTaskSpec — no external services required."""

from pathlib import Path

import pytest

from sandbox.spec import AgentTaskSpec


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

    def test_raises_when_timeout_is_zero(self):
        with pytest.raises(ValueError, match="timeout_seconds"):
            AgentTaskSpec(
                repo="https://github.com/org/repo",
                task="fix it",
                timeout_seconds=0,
            )

    def test_raises_when_timeout_negative(self):
        with pytest.raises(ValueError, match="timeout_seconds"):
            AgentTaskSpec(
                repo="https://github.com/org/repo",
                task="fix it",
                timeout_seconds=-1,
            )


class TestAgentTaskSpecDefaults:
    def test_default_values(self):
        spec = AgentTaskSpec(repo="https://github.com/org/repo", task="fix it")

        assert spec.base_branch == "main"
        assert spec.image is None
        assert spec.env == {}
        assert spec.timeout_seconds == 300
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
