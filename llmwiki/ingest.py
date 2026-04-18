"""Ingest pipeline for LLM Wiki.

This module implements the core ingest operation: reading a source document,
calling the LLM to produce a summary page and a plan for creating/updating
wiki pages, then executing that plan by creating/updating each page and
updating the index and log.

All page writes, index updates, and log appends flow through the single
run_ingest() function. The watcher, --all flag, and single-file CLI ingest
all call this same function to prevent parallel-write-path drift.

Design decisions:
    - Two-step LLM workflow: Step 1 (ingest_summary) produces a summary page
      + JSON plan. Step 2 (ingest_update) creates/updates individual pages.
    - Index updates are code-driven (hybrid): Python inserts entries using
      the LLM-generated 'reason' field as the description. No extra LLM call.
    - Defensive code-fence stripping on all LLM page output, in case the
      model wraps markdown in outer ``` fences despite prompt instructions.
"""

import json
import re
from datetime import date
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from llmwiki.config import load_config
from llmwiki.llm import call_llm
from llmwiki import wiki

console = Console()


def _load_prompt(name: str) -> str:
    """Load a prompt template from the prompts directory.

    Args:
        name: Template filename (e.g., "ingest_summary.txt").

    Returns:
        The raw template string with {placeholders} unfilled.
    """
    prompts_dir = Path(load_config()["paths"]["prompts_dir"])
    return (prompts_dir / name).read_text(encoding="utf-8")


def _load_page_template(page_type: str) -> str:
    """Load a page template for the given page type.

    Args:
        page_type: One of "concept", "source-summary", "comparison",
                   "reference", "note".

    Returns:
        The raw page template markdown string.
    """
    prompts_dir = Path(load_config()["paths"]["prompts_dir"])
    return (prompts_dir / "page_templates" / f"{page_type}.md").read_text(
        encoding="utf-8"
    )


def _strip_outer_fences(text: str) -> str:
    """Remove outer markdown code fences if the LLM wrapped the output.

    Some models (including DeepSeek V3) occasionally wrap their entire
    markdown output in ``` ... ``` code fences, even when instructed not
    to. This function defensively strips those outer fences while
    preserving any inner code fences (e.g., ```sql blocks inside the page).

    Only strips if the text starts with ``` and the very first ``` is
    NOT a ```sql or other language-tagged fence (which would indicate
    it's part of the page content, not an outer wrapper).

    Args:
        text: Raw LLM output that should be a markdown page.

    Returns:
        The text with outer fences removed, or unchanged if no outer
        fences were detected.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text

    # Check if the opening fence is language-tagged (e.g., ```sql).
    # If so, it's part of the page content — don't strip.
    first_line = stripped.split("\n", 1)[0]
    fence_tag = first_line[3:].strip()
    if fence_tag and fence_tag not in ("markdown", "md", ""):
        return text

    # Remove the opening ``` line and the closing ```
    content = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    if content.rstrip().endswith("```"):
        content = content.rstrip()[:-3].rstrip()

    return content


def run_ingest(source_path: Path, dry_run: bool = False) -> dict:
    """Execute the full ingest pipeline for a single source document.

    This is the single pipeline function that all ingest entry points
    (CLI single file, --all flag, watcher) call. It handles:
        1. Reading the source and current index
        2. Calling the LLM to produce a summary page + plan (ingest_summary)
        3. Writing the summary page
        4. Creating/updating each planned page (ingest_update)
        5. Inserting entries into index.md (code-driven)
        6. Appending to log.md

    Args:
        source_path: Path to the source file in raw/.
        dry_run: If True, show the plan without writing any files.

    Returns:
        Dict with keys:
            - pages_created: list of filenames created
            - pages_updated: list of filenames updated
            - total_tokens: int total tokens used across all LLM calls
            - title: str title of the source (from summary page)
    """
    source_content = source_path.read_text(encoding="utf-8")
    source_filename = source_path.name
    today = date.today().isoformat()
    total_tokens = 0

    # ------------------------------------------------------------------
    # Step 1: Generate summary page + plan
    # ------------------------------------------------------------------
    console.print(f"\n[bold]Ingesting:[/bold] {source_filename}")

    ingest_template = _load_prompt("ingest_summary.txt")
    source_summary_template = _load_page_template("source-summary")
    index_content = wiki.read_index()

    filled_prompt = ingest_template.format(
        today_date=today,
        source_filename=source_filename,
        index_content=index_content,
        source_content=source_content,
        source_summary_template=source_summary_template,
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Step 1/2: Generating summary + plan...", total=None)
        response = call_llm(
            system_prompt=(
                "You are a wiki maintainer. Follow the user's instructions "
                "exactly. Output only what is requested."
            ),
            user_prompt=filled_prompt,
            expect_json=True,
            max_tokens=8000,
        )
        progress.update(task, completed=True)

    total_tokens += response.total_tokens
    plan = json.loads(response.text)

    # Extract data from the plan
    summary_page = plan["summary_page"]
    pages_to_create = plan.get("pages_to_create", [])
    pages_to_update = plan.get("pages_to_update", [])

    # Parse the summary page title from its content frontmatter
    summary_meta, _ = wiki.parse_frontmatter(summary_page["content"])
    source_title = summary_meta.get("title", source_filename)

    # Display the plan
    total_touches = 1 + len(pages_to_create) + len(pages_to_update)
    console.print(f"  Summary: [cyan]{summary_page['filename']}[/cyan]")
    console.print(f"  Pages to create: [green]{len(pages_to_create)}[/green]")
    for p in pages_to_create:
        console.print(f"    + {p['filename']} — {p.get('reason', '')[:60]}")
    console.print(f"  Pages to update: [yellow]{len(pages_to_update)}[/yellow]")
    for p in pages_to_update:
        console.print(f"    ~ {p['filename']} — {p.get('changes', '')[:60]}")
    console.print(f"  Total page touches: {total_touches}")

    if dry_run:
        console.print("\n[yellow]Dry run — no files written.[/yellow]")
        return {
            "pages_created": [],
            "pages_updated": [],
            "total_tokens": total_tokens,
            "title": source_title,
        }

    # ------------------------------------------------------------------
    # Step 2: Write summary page
    # ------------------------------------------------------------------
    wiki_dir = wiki.get_wiki_dir()
    summary_path = wiki_dir / summary_page["filename"]
    wiki.write_page(summary_path, summary_page["content"])
    pages_created = [summary_page["filename"]]

    # ------------------------------------------------------------------
    # Step 3: Create and update pages
    # ------------------------------------------------------------------
    update_template = _load_prompt("ingest_update.txt")
    pages_updated = []

    all_page_ops = []
    for p in pages_to_create:
        all_page_ops.append({
            "filename": p["filename"],
            "page_type": p["type"],
            "title": p["title"],
            "operation": "create",
            "change_description": p.get("reason", "Create new page."),
            "existing_content": "",
        })
    for p in pages_to_update:
        page_path = wiki_dir / p["filename"]
        existing = ""
        if page_path.exists():
            existing = wiki.read_page(page_path)
        all_page_ops.append({
            "filename": p["filename"],
            "page_type": p["filename"].split("/")[0],
            "title": "",  # Will be parsed from existing page or plan
            "operation": "update",
            "change_description": p.get("changes", "Update based on new source."),
            "existing_content": existing,
        })

    if all_page_ops:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(
                f"Step 2/2: Creating/updating {len(all_page_ops)} pages...",
                total=len(all_page_ops),
            )

            for op in all_page_ops:
                page_template = _load_page_template(op["page_type"])

                filled_update = update_template.format(
                    today_date=today,
                    source_filename=source_filename,
                    page_filename=op["filename"],
                    page_type=op["page_type"],
                    operation=op["operation"],
                    source_excerpt=source_content,
                    change_description=op["change_description"],
                    existing_page_content=op["existing_content"],
                    page_template=page_template,
                )

                resp = call_llm(
                    system_prompt=(
                        "You are a wiki page editor. Follow the user's "
                        "instructions exactly. Output only the page content."
                    ),
                    user_prompt=filled_update,
                    expect_json=False,
                    max_tokens=4000,
                )
                total_tokens += resp.total_tokens

                # Defensive code-fence stripping
                page_content = _strip_outer_fences(resp.text)

                page_path = wiki_dir / op["filename"]
                wiki.write_page(page_path, page_content)

                if op["operation"] == "create":
                    pages_created.append(op["filename"])
                else:
                    pages_updated.append(op["filename"])

                progress.update(task, advance=1)

    # ------------------------------------------------------------------
    # Step 4: Update index (code-driven hybrid approach)
    # ------------------------------------------------------------------
    index_entries = []

    # Add the source-summary entry
    index_entries.append({
        "type": "source-summary",
        "title": source_title,
        "filename": summary_page["filename"],
        "description": f"Source summary of {source_title} ({source_filename})",
    })

    # Add entries for newly created pages
    for p in pages_to_create:
        index_entries.append({
            "type": p["type"],
            "title": p["title"],
            "filename": p["filename"],
            "description": p.get("reason", ""),
        })

    wiki.insert_index_entries(index_entries)

    # ------------------------------------------------------------------
    # Step 5: Append to log
    # ------------------------------------------------------------------
    wiki.append_log(
        operation="ingest",
        title=source_title,
        details={
            "source": source_filename,
            "pages_created": pages_created,
            "pages_updated": pages_updated,
            "tokens_used": total_tokens,
        },
    )

    console.print(
        f"\n[green]Done![/green] {len(pages_created)} created, "
        f"{len(pages_updated)} updated, {total_tokens} tokens used.\n"
    )

    return {
        "pages_created": pages_created,
        "pages_updated": pages_updated,
        "total_tokens": total_tokens,
        "title": source_title,
    }
