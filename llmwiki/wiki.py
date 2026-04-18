"""Wiki file operations module.

This module is the single point of contact for all filesystem operations
on the wiki. Every other module (ingest, query, lint) should call these
functions instead of touching files directly. This keeps file I/O logic
centralized — easier to debug, easier to change conventions later.

Design principles:
    - All paths are pathlib.Path objects (not strings) for cross-platform
      safety and cleaner manipulation.
    - The log format is constructed in code (never by the LLM) so the
      prefix format ("## [YYYY-MM-DD] operation | title") is guaranteed
      consistent and parseable with simple unix tools.
    - Functions are stateless: each call loads config fresh. This is slightly
      wasteful but keeps the module simple and avoids global state bugs.
"""

from datetime import date
from pathlib import Path

import frontmatter

from llmwiki.config import load_config


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_wiki_dir() -> Path:
    """Return the absolute path to the wiki directory.

    Resolved from the `paths.wiki_dir` field in config.yaml. This is where
    all LLM-generated markdown pages live (organized into concept/,
    source-summary/, comparison/, reference/, note/ subdirectories), plus
    the special files index.md and log.md.

    Returns:
        Absolute path to the wiki directory as a Path object.
    """
    return Path(load_config()["paths"]["wiki_dir"])


def get_raw_dir() -> Path:
    """Return the absolute path to the raw sources directory.

    Resolved from the `paths.raw_dir` field in config.yaml. This is where
    the human places source documents (articles, papers, notes) for the
    LLM to ingest. The LLM only reads from this directory; it never modifies
    files here.

    Returns:
        Absolute path to the raw directory as a Path object.
    """
    return Path(load_config()["paths"]["raw_dir"])


# ---------------------------------------------------------------------------
# Page read/write
# ---------------------------------------------------------------------------

def read_page(path: Path) -> str:
    """Read a wiki page and return its full content.

    Returns the entire file content (YAML frontmatter + markdown body) as
    a single string. To parse the frontmatter into a dict, use
    parse_frontmatter() on the returned content.

    Args:
        path: Absolute or relative path to the markdown file.

    Returns:
        Full file content as a string (UTF-8 decoded).

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    return path.read_text(encoding="utf-8")


def write_page(path: Path, content: str) -> None:
    """Write content to a wiki page, creating parent directories as needed.

    The `parents=True, exist_ok=True` arguments mean writing to a path like
    wiki/concept/new-thing.md will succeed even if the concept/ directory
    is missing — it gets created automatically. This makes the function safe
    to call without first checking that the target directory exists.

    Overwrites any existing file at that path without warning. Callers
    that need read-modify-write semantics should call read_page() first.

    Args:
        path: Absolute or relative path where the file will be written.
        content: Full markdown content to write (including any frontmatter).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Index (the wiki's table of contents)
# ---------------------------------------------------------------------------

def read_index() -> str:
    """Read the wiki's index.md file.

    The index is a catalog of all wiki pages organized by category, with
    a one-line description for each page. The LLM reads this first when
    answering queries or planning ingests, so it can decide which pages
    are relevant without scanning the whole wiki.

    If index.md does not exist (e.g., on a fresh wiki), returns a minimal
    placeholder so the LLM has something to work with.

    Returns:
        The full content of index.md as a string, or a placeholder if missing.
    """
    index_path = get_wiki_dir() / "index.md"
    if index_path.exists():
        return index_path.read_text(encoding="utf-8")
    return "# Wiki Index\n\n(empty)\n"


def update_index(content: str) -> None:
    """Overwrite index.md with new content.

    Low-level function that replaces the entire index. Prefer
    insert_index_entries() for adding entries during ingest, which
    preserves existing entries and inserts into the correct section.

    Args:
        content: Full new content for index.md.
    """
    index_path = get_wiki_dir() / "index.md"
    index_path.write_text(content, encoding="utf-8")


# Mapping from page type directory name to index section header.
_TYPE_TO_SECTION = {
    "concept": "## Concepts",
    "source-summary": "## Source Summaries",
    "comparison": "## Comparisons",
    "reference": "## References",
    "note": "## Notes",
}


def insert_index_entries(entries: list[dict]) -> None:
    """Insert new entries into index.md under the correct section headers.

    This is the code-driven (hybrid) index update strategy. The LLM
    generates descriptions during ingest (via the `reason` field in the
    plan JSON), and this function handles the deterministic insertion
    into the right section of index.md. Existing entries are never
    modified or removed.

    Each entry is inserted as a line in this format:
        - [[Title]] (type/filename.md) — description

    Args:
        entries: List of dicts, each with keys:
            - type: Page type directory name (e.g., "concept", "source-summary")
            - title: Page title in Title Case (e.g., "Window Functions")
            - filename: Relative path within wiki/ (e.g., "concept/window-functions.md")
            - description: One-line description for the index

    Raises:
        ValueError: If an entry's type doesn't match any known section.
    """
    index_content = read_index()

    for entry in entries:
        page_type = entry["type"]
        section_header = _TYPE_TO_SECTION.get(page_type)
        if not section_header:
            raise ValueError(
                f"Unknown page type '{page_type}'. "
                f"Expected one of: {list(_TYPE_TO_SECTION.keys())}"
            )

        line = f"- [[{entry['title']}]] ({entry['filename']}) — {entry['description']}"

        # Find the section header and insert after existing entries in
        # that section. A section ends at the next "## " header or EOF.
        header_pos = index_content.find(section_header)
        if header_pos == -1:
            # Section doesn't exist — append it at the end.
            index_content = index_content.rstrip() + f"\n\n{section_header}\n{line}\n"
        else:
            # Find end of this section (next ## header or EOF).
            after_header = header_pos + len(section_header)
            next_section = index_content.find("\n## ", after_header)
            if next_section == -1:
                # Last section — insert before trailing whitespace.
                insert_pos = len(index_content.rstrip())
            else:
                # Insert before the blank line preceding the next section.
                insert_pos = next_section
            index_content = (
                index_content[:insert_pos].rstrip()
                + "\n" + line
                + "\n" + index_content[insert_pos:]
            )

    update_index(index_content)


# ---------------------------------------------------------------------------
# Log (chronological record of operations)
# ---------------------------------------------------------------------------

def append_log(operation: str, title: str, details: dict) -> None:
    """Append a log entry to log.md with a standard, parseable format.

    The format is constructed entirely in code (never by the LLM) so the
    prefix is guaranteed consistent. This makes the log parseable with
    simple unix tools — for example, `grep "^## \\[" log.md | tail -5`
    extracts the last 5 entries' summary lines.

    Output format:
        ## [YYYY-MM-DD] operation | title
        - key1: value1
        - key2: value2

    Example for an ingest:
        ## [2026-04-16] ingest | SQL Joins Tutorial
        - source: raw/sql-joins-tutorial.md
        - pages_created: source-summary/sql-joins-tutorial.md, concept/inner-join.md
        - pages_updated: index.md
        - tokens_used: 45230

    If log.md does not exist, it is created with a header. The function is
    append-only — existing entries are never modified or deleted.

    Args:
        operation: Type of operation. Conventionally one of "ingest",
                   "query", or "lint", but any short string works.
        title: Short descriptor of what was operated on. For ingest, this
               is typically the source's title or filename. For query,
               the question text. For lint, a brief summary.
        details: Dict of key-value pairs to include as bullet points under
                 the entry. Values that are lists are joined with ", ".
                 Common keys: source, pages_created, pages_updated, tokens_used.
    """
    log_path = get_wiki_dir() / "log.md"

    # Initialize log.md with a header if this is the first entry
    if not log_path.exists():
        log_path.write_text("# Wiki Log\n", encoding="utf-8")

    # Build the entry. Date comes from code, never from the LLM, so the
    # format is guaranteed correct.
    today = date.today().isoformat()
    lines = [f"\n## [{today}] {operation} | {title}"]

    # Format each detail as a bullet point. List values are comma-joined
    # for readability (e.g., a list of created pages becomes one line).
    for key, value in details.items():
        if isinstance(value, list):
            value_str = ", ".join(str(v) for v in value)
        else:
            value_str = str(value)
        lines.append(f"- {key}: {value_str}")

    entry = "\n".join(lines) + "\n"

    # Append mode preserves all existing entries.
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)


# ---------------------------------------------------------------------------
# Source tracking (which raw files have been ingested)
# ---------------------------------------------------------------------------

def get_ingested_sources() -> set[str]:
    """Parse log.md to find which source filenames have been ingested.

    Scans the log for entries matching the pattern:
        ## [YYYY-MM-DD] ingest | ...
        - source: <filename>

    Returns:
        Set of source filenames (e.g., {"postgres-joins.md", "sql-ctes.md"}).
        Returns an empty set if log.md doesn't exist or has no ingest entries.
    """
    log_path = get_wiki_dir() / "log.md"
    if not log_path.exists():
        return set()

    import re
    log_content = log_path.read_text(encoding="utf-8")
    # Match "- source: <filename>" lines that appear under ingest entries.
    return set(re.findall(r"^- source: (.+)$", log_content, re.MULTILINE))


def get_pending_sources() -> list[Path]:
    """Find source files in raw/ that haven't been ingested yet.

    Compares filenames in raw/ against the set of ingested sources
    from log.md. A source is "pending" if its filename appears in
    raw/ but not in any ingest log entry.

    Only considers .md files (not images, assets, etc.).

    Returns:
        Sorted list of Paths to un-ingested source files.
    """
    raw_dir = get_raw_dir()
    ingested = get_ingested_sources()
    return sorted(
        p for p in raw_dir.glob("*.md")
        if p.name not in ingested
    )


# ---------------------------------------------------------------------------
# Page enumeration and parsing
# ---------------------------------------------------------------------------

def list_pages() -> list[Path]:
    """List all content pages in the wiki.

    Recursively finds every .md file under the wiki directory, EXCLUDING
    index.md and log.md. Those two files are special — index.md is the
    catalog and log.md is the changelog — they are not "content" pages
    and most operations (lint, query) want to operate on actual content.

    Returns:
        Sorted list of Path objects for every content page.
    """
    wiki_dir = get_wiki_dir()
    excluded = {"index.md", "log.md"}
    return sorted(
        p for p in wiki_dir.rglob("*.md")
        if p.name not in excluded
    )


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a markdown string.

    Wiki pages have frontmatter that looks like:
        ---
        title: Window Functions
        type: concept
        tags: [sql, intermediate]
        ---
        # Window Functions
        Body content...

    This function splits the two parts and returns them separately. The
    metadata is returned as a regular dict (not the python-frontmatter
    library's Post object) so callers don't need to know about that library.

    If the content has no frontmatter, returns ({}, content) — the body is
    the entire input.

    Args:
        content: Full markdown content (with or without frontmatter).

    Returns:
        Tuple of (metadata_dict, body_text). Metadata may be empty.
    """
    post = frontmatter.loads(content)
    return dict(post.metadata), post.content


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def page_stats() -> dict:
    """Compute basic statistics about the wiki.

    Used by the `llmwiki status` CLI command to give the user a quick
    overview of what's in the wiki. Counts content pages by type
    (using the parent directory name — concept/, comparison/, etc.) and
    counts source files in raw/.

    Returns:
        Dict with keys:
            total_pages: int — number of content pages (excludes index/log)
            by_type: dict[str, int] — page count keyed by directory name
            total_sources: int — number of files in raw/ (excluding hidden)
    """
    wiki_dir = get_wiki_dir()
    raw_dir = get_raw_dir()

    # Count content pages, grouping by parent directory name (= page type)
    pages = list_pages()
    by_type: dict[str, int] = {}
    for p in pages:
        # If page sits directly in wiki/ (not in a subdir), label it "root"
        page_type = p.parent.name if p.parent != wiki_dir else "root"
        by_type[page_type] = by_type.get(page_type, 0) + 1

    # Count source files in raw/, excluding hidden files (e.g., .DS_Store)
    # and only counting actual files (not subdirectories like raw/assets/).
    source_count = sum(
        1 for s in raw_dir.rglob("*")
        if s.is_file() and not s.name.startswith(".")
    )

    return {
        "total_pages": len(pages),
        "by_type": by_type,
        "total_sources": source_count,
    }
