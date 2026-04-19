"""Lint pipeline for LLM Wiki.

This module implements wiki health checks in two phases:

Phase 1 — Deterministic checks (Python, no LLM):
    - Broken [[wikilinks]] pointing to non-existent pages
    - Pages on disk but missing from index.md
    - Pages with missing required frontmatter fields
    - Root-level pages that should be in subdirectories

Phase 2 — LLM-based heuristic checks:
    - Contradictions between pages
    - Stale claims superseded by newer sources
    - Missing concept pages (substantial topics lacking coverage)
    - Weak cross-references (related pages not linking to each other)

Design decisions:
    - Deterministic checks run first (instant, free, zero false positives).
    - LLM checks are report-only — they suggest but never auto-fix.
    - Pages are batched for LLM calls if total content exceeds ~100K chars.
    - Results printed with rich formatting for readability.
"""

import re
from datetime import date
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from llmwiki.config import load_config
from llmwiki.llm import call_llm
from llmwiki import wiki

console = Console()

# Required frontmatter fields for every wiki page.
_REQUIRED_FIELDS = ["title", "type", "tags", "sources", "created", "updated"]

# Maximum total characters of page content per LLM batch.
# ~100K chars ≈ ~25K tokens, leaving room for prompt + response.
_BATCH_CHAR_LIMIT = 100_000


# ---------------------------------------------------------------------------
# Deterministic checks
# ---------------------------------------------------------------------------

def _check_broken_wikilinks() -> list[dict]:
    """Find [[wikilinks]] that point to non-existent pages.

    Scans every page for [[Page Title]] patterns, then checks whether
    any page in the wiki has a matching title in its frontmatter.

    Returns:
        List of dicts with keys: page (source filename), link (the
        broken wikilink text), message.
    """
    wiki_dir = wiki.get_wiki_dir()
    pages = wiki.list_pages()

    # Build a set of all known page titles (from frontmatter)
    known_titles: set[str] = set()
    for p in pages:
        content = wiki.read_page(p)
        meta, _ = wiki.parse_frontmatter(content)
        title = meta.get("title", "")
        if title:
            known_titles.add(title)

    # Scan every page for wikilinks and check against known titles
    issues = []
    for p in pages:
        content = wiki.read_page(p)
        wikilinks = re.findall(r"\[\[([^\]]+)\]\]", content)
        rel_path = str(p.relative_to(wiki_dir))
        for link in set(wikilinks):  # deduplicate within page
            if link not in known_titles:
                issues.append({
                    "page": rel_path,
                    "link": link,
                    "message": f"[[{link}]] — no page with this title exists",
                })

    return issues


def _check_missing_from_index() -> list[dict]:
    """Find pages on disk that are not listed in index.md.

    Compares filenames from list_pages() against filenames that appear
    in the index content via regex.

    Returns:
        List of dicts with keys: page, message.
    """
    index_content = wiki.read_index()
    pages = wiki.list_pages()
    wiki_dir = wiki.get_wiki_dir()

    # Extract all filenames mentioned in the index
    # Format in index: (type/filename.md) or (filename.md)
    indexed_files = set(re.findall(r"\(([^)]+\.md)\)", index_content))

    issues = []
    for p in pages:
        rel_path = str(p.relative_to(wiki_dir))
        if rel_path not in indexed_files:
            issues.append({
                "page": rel_path,
                "message": f"not listed in index.md",
            })

    return issues


def _check_missing_frontmatter() -> list[dict]:
    """Find pages with missing required frontmatter fields.

    Every wiki page should have: title, type, tags, sources, created,
    updated.

    Returns:
        List of dicts with keys: page, missing_fields, message.
    """
    pages = wiki.list_pages()
    wiki_dir = wiki.get_wiki_dir()
    issues = []

    for p in pages:
        content = wiki.read_page(p)
        meta, _ = wiki.parse_frontmatter(content)
        missing = [f for f in _REQUIRED_FIELDS if f not in meta]
        if missing:
            rel_path = str(p.relative_to(wiki_dir))
            issues.append({
                "page": rel_path,
                "missing_fields": missing,
                "message": f"missing fields: {', '.join(missing)}",
            })

    return issues


def _check_root_level_pages() -> list[dict]:
    """Find .md files sitting directly in wiki/ instead of a subdirectory.

    These are typically created by Obsidian when clicking a wikilink
    (Obsidian creates new pages at the vault root by default). They
    should be moved into the appropriate subdirectory.

    Returns:
        List of dicts with keys: page, message.
    """
    wiki_dir = wiki.get_wiki_dir()
    pages = wiki.list_pages()
    issues = []

    for p in pages:
        if p.parent == wiki_dir:
            issues.append({
                "page": p.name,
                "message": "root-level page — should be in a subdirectory "
                           "(concept/, reference/, note/, etc.)",
            })

    return issues


# ---------------------------------------------------------------------------
# LLM-based checks
# ---------------------------------------------------------------------------

def _load_prompt(name: str) -> str:
    """Load a prompt template from the prompts directory."""
    prompts_dir = Path(load_config()["paths"]["prompts_dir"])
    return (prompts_dir / name).read_text(encoding="utf-8")


def _build_all_pages_content() -> list[tuple[str, str]]:
    """Read all wiki pages and return as (filename, content) tuples.

    Returns:
        List of (relative_path, full_content) tuples, sorted by path.
    """
    wiki_dir = wiki.get_wiki_dir()
    pages = wiki.list_pages()
    result = []
    for p in pages:
        rel_path = str(p.relative_to(wiki_dir))
        content = wiki.read_page(p)
        result.append((rel_path, content))
    return result


def _batch_pages(pages: list[tuple[str, str]]) -> list[str]:
    """Group pages into batches that fit within the LLM context limit.

    Each batch is a concatenated string of pages with filename headers,
    ready to be inserted into the lint prompt.

    Args:
        pages: List of (filename, content) tuples.

    Returns:
        List of batch strings. Each batch is under _BATCH_CHAR_LIMIT.
    """
    batches = []
    current_batch = []
    current_size = 0

    for filename, content in pages:
        section = f"=== {filename} ===\n{content}"
        section_size = len(section)

        if current_size + section_size > _BATCH_CHAR_LIMIT and current_batch:
            batches.append("\n\n".join(current_batch))
            current_batch = []
            current_size = 0

        current_batch.append(section)
        current_size += section_size

    if current_batch:
        batches.append("\n\n".join(current_batch))

    return batches


def _run_llm_checks() -> tuple[str, int]:
    """Run LLM-based heuristic checks on wiki content.

    Sends all pages (batched if necessary) to the LLM with the lint
    prompt. Combines results from multiple batches if needed.

    Returns:
        Tuple of (report_text, total_tokens_used).
    """
    lint_template = _load_prompt("lint.txt")
    all_pages = _build_all_pages_content()

    if not all_pages:
        return "No pages to check.", 0

    batches = _batch_pages(all_pages)
    total_tokens = 0
    reports = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        for i, batch_content in enumerate(batches):
            desc = (
                f"LLM analysis ({len(all_pages)} pages)..."
                if len(batches) == 1
                else f"LLM analysis batch {i + 1}/{len(batches)}..."
            )
            task = progress.add_task(desc, total=None)

            filled_prompt = lint_template.format(
                all_pages_content=batch_content,
            )

            response = call_llm(
                system_prompt=(
                    "You are a wiki auditor. Follow the instructions exactly. "
                    "Output only the structured report."
                ),
                user_prompt=filled_prompt,
                expect_json=False,
                max_tokens=4000,
            )

            total_tokens += response.total_tokens
            reports.append(response.text)
            progress.update(task, completed=True)

    # If multiple batches, combine reports
    if len(reports) == 1:
        return reports[0], total_tokens
    else:
        combined = "# Wiki Lint Report (combined from multiple batches)\n\n"
        for i, report in enumerate(reports):
            combined += f"## Batch {i + 1}\n\n{report}\n\n"
        return combined, total_tokens


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_lint(deterministic_only: bool = False) -> dict:
    """Execute the full lint pipeline.

    Runs deterministic checks first, then optionally LLM-based checks.
    Prints results with rich formatting and appends to log.md.

    Args:
        deterministic_only: If True, skip LLM checks (faster, free).

    Returns:
        Dict with keys:
            - deterministic_issues: int total count
            - llm_report: str (or None if skipped)
            - total_tokens: int
    """
    console.print("\n[bold]Running wiki lint...[/bold]\n")

    # ------------------------------------------------------------------
    # Phase 1: Deterministic checks
    # ------------------------------------------------------------------
    console.print(Panel("[bold]Phase 1: Deterministic Checks[/bold]", style="cyan"))

    broken_links = _check_broken_wikilinks()
    missing_index = _check_missing_from_index()
    missing_fm = _check_missing_frontmatter()
    root_pages = _check_root_level_pages()

    # Print broken wikilinks
    if broken_links:
        console.print(f"\n[red]Broken Wikilinks ({len(broken_links)} found)[/red]")
        for issue in broken_links:
            console.print(f"  [red]✗[/red] {issue['page']} → {issue['message']}")
    else:
        console.print("\n[green]✓[/green] Broken Wikilinks — none found")

    # Print missing from index
    if missing_index:
        console.print(f"\n[red]Pages Missing from Index ({len(missing_index)} found)[/red]")
        for issue in missing_index:
            console.print(f"  [red]✗[/red] {issue['page']} — {issue['message']}")
    else:
        console.print("\n[green]✓[/green] Pages Missing from Index — none found")

    # Print missing frontmatter
    if missing_fm:
        console.print(
            f"\n[red]Missing Frontmatter Fields ({len(missing_fm)} found)[/red]"
        )
        for issue in missing_fm:
            console.print(f"  [red]✗[/red] {issue['page']} — {issue['message']}")
    else:
        console.print("\n[green]✓[/green] Missing Frontmatter Fields — none found")

    # Print root-level pages
    if root_pages:
        console.print(f"\n[yellow]Root-Level Pages ({len(root_pages)} found)[/yellow]")
        for issue in root_pages:
            console.print(f"  [yellow]![/yellow] {issue['page']} — {issue['message']}")
    else:
        console.print("\n[green]✓[/green] Root-Level Pages — none found")

    deterministic_total = (
        len(broken_links) + len(missing_index) + len(missing_fm) + len(root_pages)
    )
    console.print(
        f"\n[dim]Deterministic: {deterministic_total} issue(s) found[/dim]"
    )

    # ------------------------------------------------------------------
    # Phase 2: LLM-based checks
    # ------------------------------------------------------------------
    llm_report = None
    total_tokens = 0

    if not deterministic_only:
        console.print()
        console.print(Panel("[bold]Phase 2: LLM-Based Checks[/bold]", style="cyan"))

        llm_report, total_tokens = _run_llm_checks()
        console.print()
        console.print(llm_report)
        console.print(f"\n[dim]({total_tokens} tokens used)[/dim]")
    else:
        console.print(
            "\n[dim]Skipping LLM checks (--deterministic-only).[/dim]"
        )

    # ------------------------------------------------------------------
    # Log the lint operation
    # ------------------------------------------------------------------
    wiki.append_log(
        operation="lint",
        title=f"{deterministic_total} deterministic + "
              f"{'skipped' if deterministic_only else 'LLM'} checks",
        details={
            "broken_wikilinks": len(broken_links),
            "missing_from_index": len(missing_index),
            "missing_frontmatter": len(missing_fm),
            "root_level_pages": len(root_pages),
            "tokens_used": total_tokens,
        },
    )

    console.print(
        f"\n[bold]Lint complete.[/bold] "
        f"{deterministic_total} deterministic issue(s)"
        + (f", LLM report above." if not deterministic_only else ".")
    )
    console.print()

    return {
        "deterministic_issues": deterministic_total,
        "llm_report": llm_report,
        "total_tokens": total_tokens,
    }
