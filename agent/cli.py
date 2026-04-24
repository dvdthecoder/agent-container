"""agent-run CLI — run coding agent tasks in ephemeral Modal sandboxes."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from sandbox.config import ConfigError, SandboxConfig
from sandbox.result import AgentTaskResult
from sandbox.sandbox import ModalSandbox
from sandbox.spec import AgentTaskSpec

_BACKENDS = ["opencode", "claude", "gemini", "stub"]


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """agent-container — ephemeral Modal sandbox for autonomous coding agents."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command()
@click.option("--repo", required=True, help="Git repo URL (https:// or git@)")
@click.option("--task", default=None, help="Task description (inline)")
@click.option(
    "--task-file",
    default=None,
    type=click.Path(exists=True, path_type=Path),
    help="Read task from a file",
)
@click.option(
    "--backend",
    default="opencode",
    type=click.Choice(_BACKENDS),
    show_default=True,
    help="Coding agent backend",
)
@click.option("--branch", default="main", show_default=True, help="Base branch")
@click.option("--image", default=None, help="Docker image override")
@click.option(
    "--timeout",
    default=300,
    show_default=True,
    type=int,
    help="Timeout in seconds",
)
@click.option("--no-pr", is_flag=True, default=False, help="Skip PR creation")
def run(
    repo: str,
    task: str | None,
    task_file: Path | None,
    backend: str,
    branch: str,
    image: str | None,
    timeout: int,
    no_pr: bool,
) -> None:
    """Run an agent task in an ephemeral Modal sandbox."""
    # Validate task / task-file mutual exclusivity
    if task is None and task_file is None:
        raise click.UsageError("Provide either --task or --task-file.")
    if task is not None and task_file is not None:
        raise click.UsageError("Provide --task or --task-file, not both.")

    try:
        config = SandboxConfig.from_env()
    except ConfigError as exc:
        click.echo(f"Configuration error: {exc}", err=True)
        sys.exit(1)

    spec = AgentTaskSpec(
        repo=repo,
        task=task,
        task_file=task_file,
        base_branch=branch,
        image=image,
        timeout_seconds=timeout,
        create_pr=not no_pr,
        backend=backend,
    )

    _emit(f"booting Modal sandbox for {repo} ...")
    result = ModalSandbox(config).run(spec)
    _print_result(result)

    sys.exit(0 if result.success else 1)


@cli.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address")
@click.option("--port", default=8080, show_default=True, type=int, help="Port")
def dashboard(host: str, port: int) -> None:
    """Start the web dashboard."""
    try:
        import uvicorn

        from dashboard.app import app  # type: ignore[import-not-found]
    except ImportError:
        click.echo("Dashboard dependencies not installed.", err=True)
        click.echo("Run: pip install agent-container", err=True)
        sys.exit(1)

    click.echo(f"Dashboard running at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


# ------------------------------------------------------------------ helpers


def _emit(msg: str) -> None:
    """Write a status line to stderr."""
    click.echo(msg, err=True)


def _print_result(result: AgentTaskResult) -> None:
    if result.success:
        _emit(f"Done in {result.duration_seconds:.1f}s")
        if result.pr_url:
            _emit(f"PR: {result.pr_url}  {result.diff_stat}")
        elif result.diff_stat:
            _emit(f"Diff: {result.diff_stat}")
    else:
        _emit(f"Failed in {result.duration_seconds:.1f}s")
        if result.error:
            _emit(f"Error: {result.error}")

    # Always write JSON to stdout for scripting
    click.echo(result.to_json())
