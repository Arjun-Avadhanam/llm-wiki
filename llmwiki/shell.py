"""Interactive REPL shell for LLM Wiki.

Provides a persistent session where users can run commands or type
natural-language questions (auto-routed to query). Built on
prompt_toolkit for history and styled prompts, using Click's own
dispatch pipeline for command execution.

Usage:
    llmwiki shell

Inside the shell:
    - Known commands (ingest, query, lint, status, watch, help, exit)
      are dispatched to their Click handlers with full argument parsing.
    - Anything else is treated as a query — "What is a JOIN?" runs
      the query pipeline automatically.
    - Up/down arrows recall command history (persistent across sessions).
    - Ctrl+R for reverse history search.
    - Ctrl+D or 'exit' to quit.
"""

import shlex
from pathlib import Path

import click
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import NestedCompleter, PathCompleter
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.panel import Panel

from llmwiki import wiki

console = Console()

# Commands recognized by the shell. Anything not matching is chat mode.
_KNOWN_COMMANDS = {"ingest", "query", "lint", "status", "watch", "help", "exit", "quit"}

# History file stored in the project root
_HISTORY_FILE = Path(__file__).resolve().parent.parent / ".llmwiki_history"

# Prompt styling
_PROMPT_STYLE = Style.from_dict({
    "prompt": "ansicyan bold",
})

# Tab completion: commands + their flags/arguments.
# PathCompleter handles file path completion for ingest.
_COMPLETER = NestedCompleter.from_nested_dict({
    "ingest": {"--all": None, "--dry-run": None, "raw/": PathCompleter()},
    "query": {"--save": None},
    "lint": {"--deterministic-only": None},
    "status": None,
    "watch": {"--daemon": None, "--stop": None},
    "help": None,
    "exit": None,
})


def _show_welcome():
    """Display the welcome banner with wiki stats and command hints."""
    try:
        stats = wiki.page_stats()
        pending = wiki.get_pending_sources()
        stats_line = (
            f"Wiki: {stats['total_pages']} pages · "
            f"{stats['total_sources']} sources · "
            f"{'[yellow]' + str(len(pending)) + ' pending[/yellow]' if pending else '[green]All ingested[/green]'}"
        )
    except Exception:
        stats_line = "Wiki: (unable to read stats)"

    banner = (
        "\n"
        "[bold]Commands:[/bold] ingest, query, lint, status, watch, help\n"
        "Or just type a question to search your wiki.\n"
        "\n"
        "[dim]Tab[/dim] for autocomplete · [dim]↑↓[/dim] for history · [dim]Ctrl+D[/dim] to exit\n"
        "\n"
        f"{stats_line}"
    )
    console.print(Panel(banner, title="[bold cyan]LLM Wiki Shell[/bold cyan]", border_style="cyan"))


def _show_help():
    """Display the help text listing all available commands."""
    console.print("""
[bold]Commands:[/bold]
  [cyan]ingest[/cyan] <file>           Ingest a source document ([dim]--all, --dry-run[/dim])
  [cyan]query[/cyan] "question"        Ask the wiki a question ([dim]--save[/dim])
  [cyan]lint[/cyan]                    Health-check the wiki ([dim]--deterministic-only[/dim])
  [cyan]status[/cyan]                  Show wiki statistics
  [cyan]watch[/cyan]                   Watch raw/ for new files ([dim]--daemon, --stop[/dim])
  [cyan]help[/cyan]                    Show this help message
  [cyan]exit[/cyan]                    Exit the shell

Or just type any question directly — it will be treated as a query.
""")


def _set_protected_args(ctx: click.Context, args: list[str]) -> None:
    """Set protected_args on a Click context (compatible with Click 8.2+).

    Click 8.2+ made `protected_args` a read-only property wrapping the
    private `_protected_args` attribute. This helper sets the private
    attribute directly, which is the same approach click-repl uses
    (PR #132). The private attribute is stable — Click uses it internally
    throughout its codebase.

    Args:
        ctx: The Click context to modify.
        args: The argument list to set.
    """
    if hasattr(ctx, "_protected_args"):
        ctx._protected_args = args
    else:
        ctx.protected_args = args


def _get_protected_args(ctx: click.Context) -> list[str]:
    """Get protected_args from a Click context (compatible with Click 8.2+).

    Args:
        ctx: The Click context to read from.

    Returns:
        The current protected_args list.
    """
    if hasattr(ctx, "_protected_args"):
        return ctx._protected_args
    return ctx.protected_args


def _dispatch(text: str, group_ctx: click.Context) -> None:
    """Parse input and route to the appropriate Click command.

    Uses Click's full dispatch pipeline (argument parsing, subcommand
    resolution, parameter coercion) via the group's invoke() method.

    If the first word is a recognized command, dispatch to Click.
    Otherwise, treat the entire input as a query (chat mode).

    Args:
        text: The raw user input.
        group_ctx: The parent Click context for the CLI group.
    """
    try:
        parts = shlex.split(text)
    except ValueError:
        # Unmatched quotes — treat as query with raw text
        parts = ["query", text]

    if not parts:
        return

    first_word = parts[0].lower()

    # Built-in shell commands (not Click commands)
    if first_word in ("exit", "quit"):
        raise EOFError
    if first_word == "help":
        _show_help()
        return

    # Chat mode: unrecognized first word → route to query
    if first_word not in _KNOWN_COMMANDS:
        parts = ["query", text]

    # Dispatch via Click's group invoke pipeline.
    # Save and restore protected_args so the context stays clean.
    group = group_ctx.command
    old_args = _get_protected_args(group_ctx)
    try:
        _set_protected_args(group_ctx, parts)
        group.invoke(group_ctx)
    except click.ClickException as e:
        e.show()
    except SystemExit:
        pass
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    finally:
        _set_protected_args(group_ctx, old_args)


def run_shell(ctx: click.Context) -> None:
    """Start the interactive shell session.

    Args:
        ctx: The Click context from the shell command (ctx.parent
             is the CLI group context).
    """
    group_ctx = ctx.parent
    _show_welcome()

    session = PromptSession(
        history=FileHistory(str(_HISTORY_FILE)),
        style=_PROMPT_STYLE,
        message=[("class:prompt", "llmwiki> ")],
        completer=_COMPLETER,
        complete_while_typing=False,
        enable_history_search=True,
    )

    while True:
        try:
            text = session.prompt().strip()
        except KeyboardInterrupt:
            # Ctrl+C: cancel current input, don't exit
            continue
        except EOFError:
            # Ctrl+D: exit
            console.print("\n[dim]Goodbye.[/dim]")
            break

        if not text:
            continue

        _dispatch(text, group_ctx)
