"""Repository hosting provider abstractions — GitHub and GitLab.

Each provider encapsulates everything that differs between hosts:
  - How to rewrite a remote URL to embed an access token (for ``git push``)
  - How to parse ``(owner, repo_name)`` from a URL
  - Which REST API endpoint to POST to when opening a PR / MR
  - Which HTTP headers the API requires
  - The JSON payload field names for the PR / MR request
  - Which response field contains the PR / MR URL

Adding a new provider (Bitbucket, self-hosted GitLab, Gitea, …) means
adding one class here and registering it in ``_PROVIDERS`` — nothing else
in the codebase needs to change.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class RepoProvider(Protocol):
    """Structural interface for a git hosting provider."""

    name: str           # short identifier, e.g. "github" or "gitlab"
    token_env_var: str  # container env var that holds the access token

    def matches(self, repo: str) -> bool:
        """Return True if this provider handles *repo*."""
        ...

    def authed_remote(self, repo: str, token: str) -> str:
        """Rewrite *repo* to embed *token* so ``git push`` can authenticate."""
        ...

    def parse_repo(self, repo: str) -> tuple[str, str]:
        """Return ``(owner, repo_name)`` from a repository URL."""
        ...

    def pr_api_url(self, owner: str, repo_name: str) -> str:
        """REST endpoint to POST a new pull / merge request to."""
        ...

    def pr_headers(self) -> list[str]:
        """curl ``-H`` values.  May reference container env vars (e.g. ``$GITHUB_TOKEN``)."""
        ...

    def pr_payload(
        self, title: str, head_branch: str, base_branch: str, body: str
    ) -> dict:
        """JSON payload for the PR / MR creation request."""
        ...

    def pr_url_field(self) -> str:
        """Key in the API response that contains the PR / MR URL."""
        ...


# ──────────────────────────────────────────────────────────── GitHub


class GitHubProvider:
    """github.com — REST API v3, PAT or fine-grained token."""

    name = "github"
    token_env_var = "GITHUB_TOKEN"  # noqa: S105

    def matches(self, repo: str) -> bool:
        return "github.com" in repo

    def authed_remote(self, repo: str, token: str) -> str:
        if repo.startswith("https://github.com/"):
            rest = repo[len("https://"):]
            return f"https://x-access-token:{token}@{rest}"
        if repo.startswith("git@github.com:"):
            path = repo[len("git@github.com:"):]
            return f"https://x-access-token:{token}@github.com/{path}"
        raise ValueError(f"Not a GitHub URL: {repo!r}")

    def parse_repo(self, repo: str) -> tuple[str, str]:
        if repo.startswith("https://github.com/"):
            path = repo[len("https://github.com/"):].rstrip("/").removesuffix(".git")
        elif repo.startswith("git@github.com:"):
            path = repo[len("git@github.com:"):].removesuffix(".git")
        else:
            raise ValueError(f"Not a GitHub URL: {repo!r}")
        owner, name = path.split("/", 1)
        return owner, name

    def pr_api_url(self, owner: str, repo_name: str) -> str:
        return f"https://api.github.com/repos/{owner}/{repo_name}/pulls"

    def pr_headers(self) -> list[str]:
        return [
            "Authorization: token $GITHUB_TOKEN",
            "Content-Type: application/json",
            "Accept: application/vnd.github+json",
        ]

    def pr_payload(
        self, title: str, head_branch: str, base_branch: str, body: str
    ) -> dict:
        return {"title": title, "head": head_branch, "base": base_branch, "body": body}

    def pr_url_field(self) -> str:
        return "html_url"


# ──────────────────────────────────────────────────────────── GitLab


class GitLabProvider:
    """gitlab.com — REST API v4, personal access token or OAuth2 token."""

    name = "gitlab"
    token_env_var = "GITLAB_TOKEN"  # noqa: S105

    def matches(self, repo: str) -> bool:
        return "gitlab.com" in repo

    def authed_remote(self, repo: str, token: str) -> str:
        if repo.startswith("https://gitlab.com/"):
            rest = repo[len("https://"):]
            return f"https://oauth2:{token}@{rest}"
        if repo.startswith("git@gitlab.com:"):
            path = repo[len("git@gitlab.com:"):]
            return f"https://oauth2:{token}@gitlab.com/{path}"
        raise ValueError(f"Not a GitLab URL: {repo!r}")

    def parse_repo(self, repo: str) -> tuple[str, str]:
        if repo.startswith("https://gitlab.com/"):
            path = repo[len("https://gitlab.com/"):].rstrip("/").removesuffix(".git")
        elif repo.startswith("git@gitlab.com:"):
            path = repo[len("git@gitlab.com:"):].removesuffix(".git")
        else:
            raise ValueError(f"Not a GitLab URL: {repo!r}")
        owner, name = path.split("/", 1)
        return owner, name

    def pr_api_url(self, owner: str, repo_name: str) -> str:
        # GitLab identifies projects by URL-encoded "namespace/name".
        project_id = f"{owner}%2F{repo_name}"
        return f"https://gitlab.com/api/v4/projects/{project_id}/merge_requests"

    def pr_headers(self) -> list[str]:
        return [
            "PRIVATE-TOKEN: $GITLAB_TOKEN",
            "Content-Type: application/json",
        ]

    def pr_payload(
        self, title: str, head_branch: str, base_branch: str, body: str
    ) -> dict:
        return {
            "title": title,
            "source_branch": head_branch,
            "target_branch": base_branch,
            "description": body,
        }

    def pr_url_field(self) -> str:
        return "web_url"


# ──────────────────────────────────────────────────────────── registry


_PROVIDERS: list[RepoProvider] = [GitHubProvider(), GitLabProvider()]


def detect_provider(repo: str) -> RepoProvider:
    """Return the provider that handles *repo*, raising ``ValueError`` if none match."""
    for provider in _PROVIDERS:
        if provider.matches(repo):
            return provider
    supported = ", ".join(p.name for p in _PROVIDERS)
    raise ValueError(
        f"Unsupported repository host (supported: {supported}). Got: {repo!r}"
    )
