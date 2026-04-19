"""Query pipeline for LLM Wiki.

This module implements the query operation: answering questions using
the wiki's content. It works in two steps:

1. Page selection: The LLM reads the index and picks the most relevant
   pages for the question (max 5).
2. Synthesis: The LLM reads the selected pages and produces a grounded
   answer with [[wikilink]] citations.

Optionally, the answer can be saved back to the wiki as a note page
(the "explorations compound" principle from Karpathy's pattern).

Design decisions:
    - Two-step approach avoids sending the entire wiki to the LLM.
      The index is small enough to fit in context; only the relevant
      pages are loaded for synthesis.
    - The "not enough info" case is handled by the prompt itself —
      the LLM is instructed to say what's missing and suggest sources.
    - Saved notes use the note/ page type with lighter structure than
      concept pages.
"""

import json
import re
from datetime import date
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.progress import Progress, SpinnerColumn, TextColumn

from llmwiki.config import load_config
from llmwiki.llm import call_llm
from llmwiki import wiki

console = Console()


def _load_prompt(name: str) -> str:
    """Load a prompt template from the prompts directory.

    Args:
        name: Template filename (e.g., "query_select.txt").

    Returns:
        The raw template string with {placeholders} unfilled.
    """
    prompts_dir = Path(load_config()["paths"]["prompts_dir"])
    return (prompts_dir / name).read_text(encoding="utf-8")


def _build_pages_content(filenames: list[str]) -> str:
    """Read wiki pages and concatenate them with filename headers.

    Each page is delimited by a clear header so the LLM can attribute
    claims to specific pages in its response.

    Args:
        filenames: List of page paths relative to wiki/ (e.g.,
                   "concept/inner-join.md").

    Returns:
        Concatenated page content with headers, or empty string if
        no valid pages found.
    """
    wiki_dir = wiki.get_wiki_dir()
    sections = []

    for filename in filenames:
        page_path = wiki_dir / filename
        if not page_path.exists():
            console.print(f"  [yellow]Page not found: {filename}[/yellow]")
            continue
        content = wiki.read_page(page_path)
        sections.append(f"=== {filename} ===\n{content}")

    return "\n\n".join(sections)


def _slugify(text: str) -> str:
    """Convert a question or title to a kebab-case filename slug.

    Args:
        text: The text to slugify (e.g., "What is a LEFT JOIN?").

    Returns:
        A kebab-case string safe for filenames (e.g., "what-is-a-left-join").
    """
    # Lowercase, replace non-alphanumeric with hyphens, collapse runs
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    # Truncate to reasonable length for a filename
    return slug[:60]


def run_query(question: str, save: bool = False) -> str:
    """Execute the full query pipeline: select pages, synthesize answer.

    Args:
        question: The user's question.
        save: If True, file the answer as a wiki/note/ page and
              update the index.

    Returns:
        The synthesized answer text.
    """
    today = date.today().isoformat()
    total_tokens = 0

    # ------------------------------------------------------------------
    # Step 1: Page selection
    # ------------------------------------------------------------------
    select_template = _load_prompt("query_select.txt")
    index_content = wiki.read_index()

    filled_select = select_template.format(
        index_content=index_content,
        question=question,
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Finding relevant pages...", total=None)
        response = call_llm(
            system_prompt="You are a wiki assistant. Output only what is requested.",
            user_prompt=filled_select,
            expect_json=True,
            max_tokens=500,
        )
        progress.update(task, completed=True)

    total_tokens += response.total_tokens
    selection = json.loads(response.text)
    relevant_pages = selection.get("relevant_pages", [])

    if not relevant_pages:
        console.print(
            "\n[yellow]No relevant pages found in the wiki for this question.[/yellow]"
        )
        console.print(
            "Consider ingesting a source that covers this topic.\n"
        )
        return "No relevant pages found in the wiki for this question."

    console.print(f"\n[dim]Pages selected ({len(relevant_pages)}):[/dim]")
    for p in relevant_pages:
        console.print(f"  [dim]- {p}[/dim]")

    # ------------------------------------------------------------------
    # Step 2: Synthesis
    # ------------------------------------------------------------------
    pages_content = _build_pages_content(relevant_pages)

    if not pages_content:
        console.print("[red]Could not read any of the selected pages.[/red]")
        return "Error: could not read selected pages."

    synthesize_template = _load_prompt("query_synthesize.txt")
    filled_synthesize = synthesize_template.format(
        question=question,
        pages_content=pages_content,
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Synthesizing answer...", total=None)
        response = call_llm(
            system_prompt=(
                "You are a wiki assistant. Answer using only the provided "
                "wiki pages. Output only the answer."
            ),
            user_prompt=filled_synthesize,
            expect_json=False,
            max_tokens=4000,
        )
        progress.update(task, completed=True)

    total_tokens += response.total_tokens
    answer = response.text

    # Display the answer with rich markdown rendering
    console.print()
    console.print(Markdown(answer))
    console.print(f"\n[dim]({total_tokens} tokens used)[/dim]")

    # ------------------------------------------------------------------
    # Step 3: Optionally save as a wiki note
    # ------------------------------------------------------------------
    if save:
        slug = _slugify(question)
        note_filename = f"note/query-{slug}.md"
        note_path = wiki.get_wiki_dir() / note_filename

        # Build the note page with frontmatter
        pages_consulted = ", ".join(relevant_pages)
        note_content = (
            f"---\n"
            f"title: \"{question}\"\n"
            f"type: note\n"
            f"tags: [query]\n"
            f"sources: []\n"
            f"created: {today}\n"
            f"updated: {today}\n"
            f"pages_consulted: [{pages_consulted}]\n"
            f"---\n\n"
            f"# {question}\n\n"
            f"{answer}\n"
        )

        wiki.write_page(note_path, note_content)

        # Add to index
        wiki.insert_index_entries([{
            "type": "note",
            "title": question,
            "filename": note_filename,
            "description": f"Query answer — consulted {len(relevant_pages)} pages",
        }])

        # Log the query
        wiki.append_log(
            operation="query",
            title=question,
            details={
                "pages_consulted": relevant_pages,
                "answer_filed": note_filename,
                "tokens_used": total_tokens,
            },
        )

        console.print(f"\n[green]Answer saved to wiki/{note_filename}[/green]")

    return answer
