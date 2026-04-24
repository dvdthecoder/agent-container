"""Unit tests for sandbox.providers — GitHubProvider, GitLabProvider, detect_provider."""

from __future__ import annotations

import pytest

from sandbox.providers import (
    GitHubProvider,
    GitLabProvider,
    RepoProvider,
    detect_provider,
)

_gh = GitHubProvider()
_gl = GitLabProvider()


# ------------------------------------------------------------------ Protocol


def test_github_satisfies_protocol():
    assert isinstance(_gh, RepoProvider)


def test_gitlab_satisfies_protocol():
    assert isinstance(_gl, RepoProvider)


# ------------------------------------------------------------------ matches


@pytest.mark.parametrize(
    "repo",
    [
        "https://github.com/org/repo",
        "git@github.com:org/repo.git",
    ],
)
def test_github_matches(repo):
    assert _gh.matches(repo) is True


@pytest.mark.parametrize(
    "repo",
    [
        "https://gitlab.com/org/repo",
        "git@gitlab.com:org/repo.git",
    ],
)
def test_github_does_not_match_gitlab(repo):
    assert _gh.matches(repo) is False


@pytest.mark.parametrize(
    "repo",
    [
        "https://gitlab.com/org/repo",
        "git@gitlab.com:org/repo.git",
    ],
)
def test_gitlab_matches(repo):
    assert _gl.matches(repo) is True


@pytest.mark.parametrize(
    "repo",
    [
        "https://github.com/org/repo",
        "git@github.com:org/repo.git",
    ],
)
def test_gitlab_does_not_match_github(repo):
    assert _gl.matches(repo) is False


# ------------------------------------------------------------------ authed_remote


@pytest.mark.parametrize(
    "repo, expected",
    [
        (
            "https://github.com/org/myapp",
            "https://x-access-token:tok@github.com/org/myapp",
        ),
        (
            "https://github.com/org/myapp.git",
            "https://x-access-token:tok@github.com/org/myapp.git",
        ),
        (
            "git@github.com:org/myapp.git",
            "https://x-access-token:tok@github.com/org/myapp.git",
        ),
    ],
)
def test_github_authed_remote(repo, expected):
    assert _gh.authed_remote(repo, "tok") == expected


def test_github_authed_remote_wrong_host_raises():
    with pytest.raises(ValueError, match="Not a GitHub URL"):
        _gh.authed_remote("https://gitlab.com/org/repo", "tok")


@pytest.mark.parametrize(
    "repo, expected",
    [
        (
            "https://gitlab.com/org/myapp",
            "https://oauth2:tok@gitlab.com/org/myapp",
        ),
        (
            "https://gitlab.com/org/myapp.git",
            "https://oauth2:tok@gitlab.com/org/myapp.git",
        ),
        (
            "git@gitlab.com:org/myapp.git",
            "https://oauth2:tok@gitlab.com/org/myapp.git",
        ),
    ],
)
def test_gitlab_authed_remote(repo, expected):
    assert _gl.authed_remote(repo, "tok") == expected


def test_gitlab_authed_remote_wrong_host_raises():
    with pytest.raises(ValueError, match="Not a GitLab URL"):
        _gl.authed_remote("https://github.com/org/repo", "tok")


# ------------------------------------------------------------------ parse_repo


@pytest.mark.parametrize(
    "repo, expected",
    [
        ("https://github.com/org/myapp", ("org", "myapp")),
        ("https://github.com/org/myapp.git", ("org", "myapp")),
        ("https://github.com/org/myapp/", ("org", "myapp")),
        ("git@github.com:org/myapp.git", ("org", "myapp")),
        ("git@github.com:org/myapp", ("org", "myapp")),
    ],
)
def test_github_parse_repo(repo, expected):
    assert _gh.parse_repo(repo) == expected


def test_github_parse_repo_wrong_host_raises():
    with pytest.raises(ValueError, match="Not a GitHub URL"):
        _gh.parse_repo("https://gitlab.com/org/repo")


@pytest.mark.parametrize(
    "repo, expected",
    [
        ("https://gitlab.com/org/myapp", ("org", "myapp")),
        ("https://gitlab.com/org/myapp.git", ("org", "myapp")),
        ("https://gitlab.com/org/myapp/", ("org", "myapp")),
        ("git@gitlab.com:org/myapp.git", ("org", "myapp")),
        ("git@gitlab.com:org/myapp", ("org", "myapp")),
    ],
)
def test_gitlab_parse_repo(repo, expected):
    assert _gl.parse_repo(repo) == expected


def test_gitlab_parse_repo_wrong_host_raises():
    with pytest.raises(ValueError, match="Not a GitLab URL"):
        _gl.parse_repo("https://github.com/org/repo")


# ------------------------------------------------------------------ API URL


def test_github_pr_api_url():
    assert _gh.pr_api_url("org", "myapp") == "https://api.github.com/repos/org/myapp/pulls"


def test_gitlab_mr_api_url():
    url = _gl.pr_api_url("org", "myapp")
    assert url == "https://gitlab.com/api/v4/projects/org%2Fmyapp/merge_requests"


# ------------------------------------------------------------------ pr_payload


def test_github_pr_payload_keys():
    p = _gh.pr_payload("Fix bug", "agent/fix", "main", "body text")
    assert p == {"title": "Fix bug", "head": "agent/fix", "base": "main", "body": "body text"}


def test_gitlab_mr_payload_keys():
    p = _gl.pr_payload("Fix bug", "agent/fix", "main", "body text")
    assert p == {
        "title": "Fix bug",
        "source_branch": "agent/fix",
        "target_branch": "main",
        "description": "body text",
    }


# ------------------------------------------------------------------ pr_url_field


def test_github_pr_url_field():
    assert _gh.pr_url_field() == "html_url"


def test_gitlab_mr_url_field():
    assert _gl.pr_url_field() == "web_url"


# ------------------------------------------------------------------ pr_headers


def test_github_headers_include_auth_and_accept():
    headers = _gh.pr_headers()
    assert any("GITHUB_TOKEN" in h for h in headers)
    assert any("vnd.github" in h for h in headers)


def test_gitlab_headers_include_private_token():
    headers = _gl.pr_headers()
    assert any("PRIVATE-TOKEN" in h for h in headers)
    assert any("GITLAB_TOKEN" in h for h in headers)


# ------------------------------------------------------------------ detect_provider


def test_detect_provider_github():
    provider = detect_provider("https://github.com/org/repo")
    assert provider.name == "github"


def test_detect_provider_github_ssh():
    provider = detect_provider("git@github.com:org/repo.git")
    assert provider.name == "github"


def test_detect_provider_gitlab():
    provider = detect_provider("https://gitlab.com/org/repo")
    assert provider.name == "gitlab"


def test_detect_provider_gitlab_ssh():
    provider = detect_provider("git@gitlab.com:org/repo.git")
    assert provider.name == "gitlab"


def test_detect_provider_unsupported_raises():
    with pytest.raises(ValueError, match="Unsupported repository host"):
        detect_provider("https://bitbucket.org/org/repo")
