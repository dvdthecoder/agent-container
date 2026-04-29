"""agent-run CLI — run coding agent tasks in ephemeral Modal sandboxes."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from agent.log_store import RunStore
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
@click.argument("run_id", required=False)
@click.option("--level", default=None, help="Filter events by level (info|warn|error)")
@click.option("--phase", default=None, help="Filter events by phase (BOOTING|CLONING|RUNNING…)")
@click.option("--source", default=None, help="Filter events by source (sandbox:stderr, acp…)")
@click.option("--db", default=None, type=click.Path(path_type=Path), help="Path to runs.db")
@click.option("-n", "--limit", default=20, show_default=True, help="Rows to show in list view")
def logs(
    run_id: str | None,
    level: str | None,
    phase: str | None,
    source: str | None,
    db: Path | None,
    limit: int,
) -> None:
    """Inspect run logs stored in the local SQLite database.

    \b
    List recent runs:
        agent-run logs

    Show all events for a run:
        agent-run logs run-20260429-143022-abc123

    Filter to errors only:
        agent-run logs <run-id> --level error

    Filter to a specific phase:
        agent-run logs <run-id> --phase RUNNING
    """
    store = RunStore(db_path=db)

    try:
        if run_id is None:
            # List view
            runs = store.list_runs(limit=limit)
            if not runs:
                click.echo("No runs recorded yet.")
                return
            header = f"{'RUN ID':<32}  {'STARTED':<24}  {'OUTCOME':<9}  {'DUR':>6}  REPO"
            click.echo(header)
            click.echo("-" * len(header))
            for r in runs:
                dur = f"{r.duration_s:.1f}s" if r.duration_s is not None else "…"
                outcome = r.outcome or "running"
                started = r.started_at[:19].replace("T", " ")
                repo_short = r.repo.split("/")[-2] + "/" + r.repo.split("/")[-1]
                click.echo(f"{r.run_id:<32}  {started:<24}  {outcome:<9}  {dur:>6}  {repo_short}")
        else:
            # Event view
            run = store.get_run(run_id)
            if run is None:
                click.echo(f"Run not found: {run_id}", err=True)
                sys.exit(1)

            outcome = run.outcome or "in progress"
            dur = f"{run.duration_s:.1f}s" if run.duration_s is not None else "…"
            click.echo(f"run_id   : {run.run_id}")
            click.echo(f"repo     : {run.repo}")
            click.echo(f"task     : {run.task[:80]}")
            click.echo(f"backend  : {run.backend}")
            click.echo(f"started  : {run.started_at}")
            click.echo(f"outcome  : {outcome}  ({dur})")
            if run.pr_url:
                click.echo(f"pr       : {run.pr_url}")
            if run.sandbox_id:
                click.echo(f"sandbox  : {run.sandbox_id}")
            click.echo("")

            events = store.events(run_id, level=level, phase=phase, source=source)
            if not events:
                click.echo("No events match the filter.")
                return

            for ev in events:
                ts = ev.ts[11:23]  # HH:MM:SS.mmm
                lvl = ev.level.upper()[:4]
                color = "red" if ev.level == "error" else ("yellow" if ev.level == "warn" else None)
                line = f"[{ts}] {ev.elapsed_s:>7.2f}s  {ev.phase:<8}  {ev.source:<20}  {lvl}  {ev.message}"  # noqa: E501
                click.echo(click.style(line, fg=color) if color else line)

    except FileNotFoundError as exc:
        click.echo(str(exc), err=True)
        click.echo("No runs have been recorded yet — run `agent-run run` first.", err=True)
        sys.exit(1)


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
