"""CLI entry point for LLM Wiki."""

from pathlib import Path

import click
from rich.console import Console

from llmwiki import wiki

console = Console()


def _check_pending_sources():
    """Print a notice if there are un-ingested files in raw/.

    Called before every command so the user is aware of pending sources
    without needing to run a separate check. This is the "pre-command
    check" from Option C in the design — zero background processes,
    just a scan on each CLI invocation.
    """
    try:
        pending = wiki.get_pending_sources()
        if pending:
            console.print(
                f"\n[yellow]![/yellow] {len(pending)} un-ingested file(s) in raw/:"
            )
            for p in pending[:5]:
                console.print(f"    - {p.name}")
            if len(pending) > 5:
                console.print(f"    ... and {len(pending) - 5} more")
            console.print(
                "    Run [bold]llmwiki ingest --all[/bold] to ingest them.\n"
            )
    except Exception:
        # Don't let a check failure block the actual command.
        pass


@click.group()
@click.version_option(version="0.1.0")
@click.option("--verbose", is_flag=True, help="Print LLM prompts and responses for debugging.")
def cli(verbose):
    """llmwiki — LLM-maintained personal knowledge base."""
    if verbose:
        from llmwiki import llm
        llm.verbose = True
    _check_pending_sources()


@cli.command()
@click.argument("source_file", type=click.Path(exists=True), required=False)
@click.option("--dry-run", is_flag=True, help="Show the ingest plan without writing files.")
@click.option("--all", "ingest_all", is_flag=True, help="Ingest all un-ingested files in raw/.")
def ingest(source_file, dry_run, ingest_all):
    """Ingest a source document into the wiki.

    Provide a path to a source file, or use --all to ingest every
    un-ingested file in raw/.
    """
    from llmwiki.ingest import run_ingest

    if ingest_all:
        pending = wiki.get_pending_sources()
        if not pending:
            console.print("[green]No pending sources to ingest.[/green]")
            return
        console.print(f"[bold]Ingesting {len(pending)} source(s)...[/bold]")
        for i, source_path in enumerate(pending, 1):
            console.print(f"\n[dim]--- [{i}/{len(pending)}] ---[/dim]")
            run_ingest(source_path, dry_run=dry_run)
    elif source_file:
        run_ingest(Path(source_file), dry_run=dry_run)
    else:
        console.print("[red]Provide a source file path or use --all.[/red]")
        raise SystemExit(1)


@cli.command()
@click.argument("question")
@click.option("--save", is_flag=True, help="Save the answer as a wiki note page.")
def query(question, save):
    """Ask a question against the wiki."""
    from llmwiki.query import run_query
    run_query(question, save=save)


@cli.command()
@click.option(
    "--deterministic-only", is_flag=True,
    help="Run only deterministic checks (no LLM call, instant and free).",
)
def lint(deterministic_only):
    """Health-check the wiki for issues."""
    from llmwiki.lint import run_lint
    run_lint(deterministic_only=deterministic_only)


@cli.command()
def status():
    """Show wiki statistics."""
    stats = wiki.page_stats()
    console.print("\n[bold]Wiki Status[/bold]")
    console.print(f"  Total pages: {stats['total_pages']}")
    if stats["by_type"]:
        for page_type, count in sorted(stats["by_type"].items()):
            console.print(f"    {page_type}: {count}")
    console.print(f"  Total sources in raw/: {stats['total_sources']}")

    pending = wiki.get_pending_sources()
    if pending:
        console.print(f"  [yellow]Pending ingest: {len(pending)} file(s)[/yellow]")
    else:
        console.print(f"  [green]All sources ingested[/green]")
    console.print()


@cli.command()
@click.option("--daemon", is_flag=True, help="Run in the background (detached).")
@click.option("--stop", is_flag=True, help="Stop a running background watcher.")
def watch(daemon, stop):
    """Watch raw/ for new files and auto-ingest them."""
    from llmwiki.watcher import run_watcher, stop_watcher

    if stop:
        stop_watcher()
    elif daemon:
        run_watcher(daemon=True)
    else:
        run_watcher(daemon=False)


if __name__ == "__main__":
    cli()
