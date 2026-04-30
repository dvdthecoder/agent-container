"""Git operations that run inside a Modal sandbox workspace.

All functions receive a ``modal.Sandbox`` instance and execute git commands
inside it via ``sb.exec``.  Nothing here touches the local filesystem.
"""

from __future__ import annotations

import json
import shlex
from datetime import UTC, datetime

import modal
from sandbox.config import ConfigError, SandboxConfig
from sandbox.providers import RepoProvider, detect_provider

# ── Public API ────────────────────────────────────────────────────────────────


def branch_name(backend: str) -> str:
    """Return a time-stamped branch name for an agent run.

    Format: ``agent/<backend>-YYYYMMDD-HHMMSS``
    """
    ts = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    return f"agent/{backend}-{ts}"


def clone(sb: modal.Sandbox, repo: str, base_branch: str) -> None:
    """Clone *repo* at *base_branch* into ``/workspace`` inside the sandbox."""
    proc = sb.exec(
        "git",
        "clone",
        "--branch",
        base_branch,
        "--depth",
        "1",
        repo,
        "/workspace",
    )
    # Read stderr before wait() — Modal drains streams on wait(), so reading
    # after would return empty even when there is an error message.
    stderr = proc.stderr.read()
    proc.wait()
    if proc.returncode != 0:
        raise ConfigError(f"git clone failed:\n{stderr}")

    # Exclude common build artifacts from git tracking.
    # .git/info/exclude is local-only — never committed — so we don't modify
    # the target repo's .gitignore.  This prevents pytest __pycache__ / .pyc
    # files from appearing in collect_diff and polluting the PR diff.
    exclude_proc = sb.exec(
        "sh",
        "-c",
        "printf '__pycache__/\\n*.pyc\\n*.pyo\\n.pytest_cache/\\n' >> /workspace/.git/info/exclude",
    )
    exclude_proc.wait()


def collect_diff(
    sb: modal.Sandbox,
    base_branch: str = "main",
    workdir: str = "/workspace",
) -> tuple[str, str]:
    """Return ``(full_diff, diff_stat)`` for all changes since clone.

    Compares against ``origin/<base_branch>`` so aider commits are included —
    ``git diff HEAD`` would return empty if aider already committed its edits.
    """
    ref = f"origin/{base_branch}"
    diff_proc = sb.exec("git", "diff", ref, workdir=workdir)
    diff = diff_proc.stdout.read()
    diff_proc.wait()

    stat_proc = sb.exec("git", "diff", "--stat", ref, workdir=workdir)
    stat = stat_proc.stdout.read()
    stat_proc.wait()

    return diff, stat.strip()


def push_and_pr(
    sb: modal.Sandbox,
    repo: str,
    base_branch: str,
    backend: str,
    task: str,
    config: SandboxConfig,
    workdir: str = "/workspace",
) -> tuple[str, str | None]:
    """Stage, commit, push on a new branch, and open a PR / MR.

    Returns ``(branch_name, pr_url)``.  ``pr_url`` is ``None`` when the host
    is unsupported or the access token is not configured.
    """
    br = branch_name(backend)

    try:
        provider = detect_provider(repo)
    except ValueError:
        return br, None  # unsupported host — skip push and PR

    token = config.token_for(provider.name)
    if not token:
        return br, None  # no token — skip push and PR

    # git identity is required to create a commit inside the container.
    _git(sb, ["config", "user.email", "agent@agent-container"], workdir)
    _git(sb, ["config", "user.name", "Agent Container"], workdir)
    _git(sb, ["checkout", "-b", br], workdir)
    _git(sb, ["add", "-A"], workdir)
    # Only commit if there is something staged — aider may have already committed.
    status_proc = sb.exec("git", "diff", "--cached", "--quiet", workdir=workdir)
    status_proc.wait()
    if status_proc.returncode != 0:  # non-zero → staged changes exist
        _git(sb, ["commit", "-m", f"agent: {task[:72]}"], workdir)

    # Rewrite the remote URL to embed the token so `git push` can authenticate.
    authed_url = provider.authed_remote(repo, token)
    _git(sb, ["remote", "set-url", "origin", authed_url], workdir)

    push_proc = sb.exec("git", "push", "origin", br, workdir=workdir)
    push_proc.wait()
    if push_proc.returncode != 0:
        raise ConfigError(f"git push failed:\n{push_proc.stderr.read()}")

    owner, repo_name = provider.parse_repo(repo)
    pr_url = _open_pr(sb, provider, owner, repo_name, br, base_branch, backend, task, workdir)
    return br, pr_url


# ── Private helpers ───────────────────────────────────────────────────────────


def _git(sb: modal.Sandbox, args: list[str], workdir: str) -> None:
    """Run a git sub-command in *workdir*, raising ``ConfigError`` on failure."""
    proc = sb.exec("git", *args, workdir=workdir)
    proc.wait()
    if proc.returncode != 0:
        raise ConfigError(f"git {args[0]} failed:\n{proc.stderr.read()}")


def _open_pr(
    sb: modal.Sandbox,
    provider: RepoProvider,
    owner: str,
    repo_name: str,
    br: str,
    base_branch: str,
    backend: str,
    task: str,
    workdir: str,
) -> str | None:
    """Open a PR / MR via the provider REST API. Returns the PR / MR URL."""
    payload = json.dumps(
        provider.pr_payload(
            title=task[:72],
            head_branch=br,
            base_branch=base_branch,
            body=f"Automated change by agent-container (`{backend}`).\n\n**Task:**\n{task}",
        )
    )

    # Write payload to a temp file so we don't need to quote JSON in a shell string.
    write_proc = sb.exec(
        "sh",
        "-c",
        f"printf '%s' {shlex.quote(payload)} > /tmp/pr_payload.json",
    )
    write_proc.wait()

    # Build the curl command with provider-specific headers.
    # Headers may reference container env vars (e.g. $GITHUB_TOKEN) which the
    # shell expands because the whole command runs under `sh -c`.
    header_flags = " ".join(f'-H "{h}"' for h in provider.pr_headers())
    api_url = provider.pr_api_url(owner, repo_name)
    curl_proc = sb.exec(
        "sh",
        "-c",
        f"curl -sf -X POST {header_flags} {api_url} -d @/tmp/pr_payload.json",
    )
    response = curl_proc.stdout.read()
    curl_proc.wait()
    if curl_proc.returncode != 0:
        raise ConfigError(f"PR creation failed:\n{curl_proc.stderr.read()}")

    data = json.loads(response)
    return data.get(provider.pr_url_field())
