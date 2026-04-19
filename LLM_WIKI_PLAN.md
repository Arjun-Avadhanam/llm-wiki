# LLM Wiki — 1-Week Implementation Plan

A Python CLI tool that implements Karpathy's LLM Wiki pattern: an LLM-maintained, persistent knowledge base of interlinked markdown files, viewed through Obsidian, powered by OpenRouter.

---

## 1. Project Overview

We are building `llmwiki`, a Python CLI that manages a personal knowledge wiki. The LLM (via OpenRouter) ingests source documents, writes structured markdown pages, maintains an index and log, answers synthesis questions across the wiki, and performs periodic health checks. Obsidian serves as the viewer/IDE. The initial domain is SQL learning, with a schema flexible enough to support general knowledge topics.

**End-state by day 7:** A working CLI where you can run `llmwiki ingest <file>`, `llmwiki query "question"`, and `llmwiki lint`, see pages appear and update in Obsidian in real-time, and browse a growing interlinked wiki with graph view.

---

## 2. Requirements

### Software

| Tool | Purpose | Notes |
|------|---------|-------|
| **Python 3.10+** | CLI tool runtime | Already installed |
| **Obsidian** | Wiki viewer/IDE | Free, install from obsidian.md |
| **Git** | Version history for the wiki | The wiki directory is a git repo |
| **OpenRouter account** | LLM API access | Sign up at openrouter.ai, add ~$5 credit |

### Model

| Model | Why |
|-------|-----|
| **DeepSeek V3** (via OpenRouter) | Best quality-to-cost ratio. ~$0.14/$0.28 per 1M tokens (input/output). Strong instruction following, good structured output, 128K context. Estimated cost: $1-3/month with daily usage. |
| **Fallback: Qwen 2.5 72B** | If DeepSeek V3 quality is insufficient for any operation, swap to this. Slightly more expensive (~$0.30/$0.50 per 1M tokens) but excellent at structured markdown generation. |

The model choice is configured in a single config variable — swapping models requires changing one line.

### Key Libraries

| Library | Purpose |
|---------|---------|
| **`openai`** | Python SDK for calling OpenRouter (OpenRouter exposes an OpenAI-compatible API, so the official `openai` library works directly with `base_url="https://openrouter.ai/api/v1"`) |
| **`click`** | CLI framework — cleaner than argparse for building `llmwiki ingest/query/lint` subcommands |
| **`python-frontmatter`** | Reading/writing YAML frontmatter in markdown files (every wiki page has frontmatter with metadata) |
| **`pyyaml`** | YAML parsing for config file |
| **`rich`** | Terminal output formatting — progress indicators during ingest, colored output for query results |

No heavy ML/data libraries needed. The LLM runs remotely; this tool is purely orchestration and file management.

### Obsidian Plugins

| Plugin | Purpose | Priority |
|--------|---------|----------|
| **Graph View** | Visualize wiki structure, find hubs and orphans | Core (built-in) |
| **Dataview** | Query pages by frontmatter fields (e.g., "all concept pages tagged SQL") | Core |
| **Obsidian Web Clipper** | Browser extension to clip articles as markdown into `raw/` | Nice-to-have |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────┐
│                   YOU (human)                        │
│  - Drop sources into raw/                           │
│  - Run CLI commands                                 │
│  - Browse wiki in Obsidian                          │
└──────────┬──────────────────────────┬───────────────┘
           │                          │
           ▼                          ▼
   ┌──────────────┐          ┌──────────────┐
   │  llmwiki CLI  │          │   Obsidian   │
   │  (Python)     │          │   (viewer)   │
   │               │          │              │
   │  ingest       │          │  Graph View  │
   │  query        │─────────▶│  Dataview    │
   │  lint         │  writes  │  Live reload │
   └──────┬───────┘  .md     └──────────────┘
          │  files
          │
          ▼
   ┌──────────────┐
   │  OpenRouter   │
   │  (DeepSeek V3)│
   │  LLM API      │
   └──────────────┘

Directory layout:

llm-wiki/                        ← project root (git repo)
├── llmwiki/                     ← Python CLI source code
│   ├── __init__.py
│   ├── cli.py                   ← click CLI entry point
│   ├── config.py                ← model, API key, paths
│   ├── ingest.py                ← ingest pipeline
│   ├── query.py                 ← query pipeline
│   ├── lint.py                  ← lint pipeline
│   ├── llm.py                   ← LLM API wrapper (call, retry, parse)
│   ├── wiki.py                  ← wiki file read/write helpers
│   └── prompts/                 ← prompt templates (text files)
│       ├── ingest_summary.txt
│       ├── ingest_update.txt
│       ├── query.txt
│       └── lint.txt
├── raw/                         ← source documents (human curates, LLM reads only)
│   └── assets/                  ← images from clipped articles
├── wiki/                        ← LLM-generated wiki (Obsidian vault root)
│   ├── concept/                 ← concept pages (e.g., window-functions.md)
│   ├── source-summary/          ← one page per ingested source
│   ├── comparison/              ← comparison pages (e.g., inner-vs-outer-join.md)
│   ├── reference/               ← quick-reference pages (syntax, cheatsheets)
│   ├── note/                    ← ad-hoc notes, filed query answers
│   ├── index.md                 ← master catalog of all pages
│   └── log.md                   ← chronological record of operations
├── config.yaml                  ← API key, model, paths
├── requirements.txt
└── README.md
```

**Operation flows:**

**Ingest:** `llmwiki ingest raw/sql-joins-tutorial.md`
1. CLI reads the source file
2. CLI reads current `index.md` to understand existing wiki state
3. LLM call #1: Generate a summary page → write to `wiki/source-summary/`
4. LLM call #2-N: For each existing page that needs updating (identified from index), send (source excerpt + existing page) → write updated page
5. CLI updates `index.md` with new/modified entries
6. CLI appends to `log.md`
7. Obsidian picks up all changes instantly

**Query:** `llmwiki query "What are the different types of JOINs?"`
1. CLI reads `index.md`
2. LLM call #1: Given the index + question, identify relevant pages
3. CLI reads those pages
4. LLM call #2: Given the pages + question, synthesize an answer
5. Answer printed to terminal
6. Optional: `--save` flag files the answer as a `wiki/note/` page

**Lint:** `llmwiki lint`
1. CLI reads all wiki pages
2. LLM call: Given all pages (or batched), check for contradictions, orphans, stale claims, missing pages
3. Report printed to terminal with suggested fixes

---

## 4. Schema Specification

This defines the wiki's structure and conventions. It is the most important design artifact — it determines the quality of the LLM's output.

### Page types

**Concept page** (`wiki/concept/<name>.md`)
```markdown
---
title: Window Functions
type: concept
tags: [sql, analytics, intermediate]
sources: [sql-window-functions-guide.md]
created: 2026-04-14
updated: 2026-04-14
---

# Window Functions

Brief definition (1-2 sentences).

## How It Works

Core explanation with examples.

## Syntax

```sql
SELECT column, FUNC() OVER (PARTITION BY col ORDER BY col) FROM table;
```

## Common Use Cases

- Use case 1
- Use case 2

## Common Mistakes

- Mistake 1 and how to avoid it

## Related Concepts

- [[Aggregate Functions]] — how window functions differ
- [[PARTITION BY]] — used within window functions
- [[CTEs]] — often combined with window functions
```

**Source summary** (`wiki/source-summary/<name>.md`)
```markdown
---
title: "SQL Joins Tutorial — W3Schools"
type: source-summary
source_file: sql-joins-tutorial.md
tags: [sql, joins, beginner]
created: 2026-04-14
---

# SQL Joins Tutorial — W3Schools

## Key Takeaways

- Bullet point 1
- Bullet point 2

## Summary

2-3 paragraph summary of the source.

## Concepts Covered

- [[INNER JOIN]]
- [[LEFT JOIN]]
- [[Self Join]]

## Notable Quotes / Examples

> Relevant quote from source

## My Assessment

Brief note on quality, bias, or gaps in the source.
```

**Comparison page** (`wiki/comparison/<name>.md`)
```markdown
---
title: INNER JOIN vs OUTER JOIN
type: comparison
tags: [sql, joins]
created: 2026-04-14
---

# INNER JOIN vs OUTER JOIN

## Quick Answer

One sentence for someone in a hurry.

## Comparison Table

| Aspect | INNER JOIN | OUTER JOIN |
|--------|-----------|------------|
| Returns | Only matching rows | Matching + unmatched rows |
| NULL handling | Excludes NULLs | Includes NULLs |
| Use case | ... | ... |

## When to Use Each

Explanation with examples.

## Related

- [[INNER JOIN]]
- [[LEFT JOIN]]
- [[FULL OUTER JOIN]]
```

**Reference page** (`wiki/reference/<name>.md`) — concise syntax cheatsheets, function lists. Minimal prose, maximum utility.

**Note page** (`wiki/note/<name>.md`) — filed query answers, insights, ad-hoc observations. Lighter structure, just frontmatter + free-form content.

### index.md format

```markdown
# Wiki Index

## Concepts
- [[Window Functions]] — SQL functions that operate over a set of rows related to the current row
- [[INNER JOIN]] — Returns only rows with matching values in both tables
- [[Normalization]] — Process of organizing data to reduce redundancy

## Source Summaries
- [[SQL Joins Tutorial — W3Schools]] — Beginner overview of join types with examples (source: sql-joins-tutorial.md)

## Comparisons
- [[INNER JOIN vs OUTER JOIN]] — When to use each join type

## References
- [[PostgreSQL String Functions]] — Cheatsheet of string manipulation functions

## Notes
- [[Query — Types of JOINs]] — Synthesis of join types across multiple sources
```

### log.md format

```markdown
# Wiki Log

## [2026-04-14] ingest | SQL Joins Tutorial — W3Schools
- Source: raw/sql-joins-tutorial.md
- Pages created: source-summary/sql-joins-tutorial.md, concept/inner-join.md, concept/left-join.md
- Pages updated: index.md, concept/joins-overview.md
- Token usage: ~45K

## [2026-04-14] query | "What are the different types of JOINs?"
- Pages consulted: concept/inner-join.md, concept/left-join.md, concept/full-outer-join.md
- Answer filed: note/query-types-of-joins.md
```

### Cross-referencing rules

- Use `[[Page Title]]` wikilink syntax (Obsidian native)
- Create a new concept page when a concept is mentioned in 2+ existing pages but doesn't have its own page yet
- Every source summary must link to concept pages it covers
- Every concept page must link to related concepts under a "Related Concepts" section
- The LLM should flag when it identifies a concept that deserves its own page but doesn't create one unsolicited — the ingest prompt explicitly lists which pages to create/update

### Naming conventions

- Filenames: kebab-case, no spaces (e.g., `window-functions.md`)
- Page titles in frontmatter: Title Case
- Tags: lowercase, comma-separated in frontmatter
- Dates: ISO 8601 (`2026-04-14`)

---

## 5. Prompt Templates

These are starting-point drafts. You will iterate on them during days 2-4 as you see real output quality.

### Ingest — Step 1: Summary + page plan

```
You are a wiki maintainer. You manage a structured knowledge wiki in markdown.

Here is the current wiki index:
---
{index_content}
---

Here is a new source document to ingest:
---
{source_content}
---

Do the following:
1. Write a source summary page following this exact template:
{source_summary_template}

2. List which EXISTING wiki pages need updating based on this source (reference the index above). For each, state what specific information should be added or revised.

3. List any NEW concept/comparison/reference pages that should be created. For each, state why it deserves its own page (the concept must be substantial enough, not just a passing mention).

Output as JSON:
{
  "summary_page": { "filename": "...", "content": "..." },
  "pages_to_update": [
    { "filename": "...", "changes": "description of what to add/revise" }
  ],
  "pages_to_create": [
    { "filename": "...", "type": "concept|comparison|reference", "reason": "..." }
  ]
}
```

### Ingest — Step 2: Update/create individual page

```
You are a wiki maintainer. You are updating a single wiki page based on a new source.

Here is the existing page:
---
{existing_page_content}
---

Here is the relevant excerpt from the new source:
---
{source_excerpt}
---

Changes to make: {change_description}

Rules:
- Preserve existing content unless it contradicts the new source (flag contradictions explicitly)
- Add new information in the appropriate section
- Update the "sources" list in frontmatter
- Update the "updated" date in frontmatter
- Maintain all existing [[wikilinks]] and add new ones where appropriate
- Keep the same page structure and template

Output the complete updated page content (full markdown with frontmatter).
```

### Query

```
You are a wiki assistant. You answer questions using the wiki's content.

Here is the wiki index:
---
{index_content}
---

The user asks: {question}

Step 1: Based on the index, list the page filenames most relevant to this question (max 5).
Output as JSON: { "relevant_pages": ["filename1", "filename2", ...] }
```

*(After retrieving pages:)*

```
Here are the relevant wiki pages:
---
{pages_content}
---

The user asks: {question}

Synthesize a clear, comprehensive answer using the wiki content. Cite specific pages using [[Page Title]] links. If the wiki doesn't have enough information to fully answer, say what's missing.
```

### Lint

```
You are a wiki auditor. Review these wiki pages for quality issues.

{all_pages_content}

Check for:
1. CONTRADICTIONS: Claims in one page that conflict with claims in another. Cite both pages and the conflicting statements.
2. ORPHAN PAGES: Pages that are not linked from any other page (check [[wikilinks]]).
3. STALE CLAIMS: Information that may be outdated based on newer sources (compare source dates).
4. MISSING PAGES: Concepts mentioned via [[wikilinks]] that don't have their own page yet.
5. WEAK CROSS-REFERENCES: Pages that discuss related topics but don't link to each other.

Output a structured report with sections for each issue type. For each issue, be specific — cite page names, line content, and suggested fixes.
```

---

## 6. Day-by-Day Breakdown

### Day 1 — Scaffolding & Setup (~2.5h)

**Goal:** Project skeleton is in place, API works, Obsidian shows the wiki.

| Task | Time | Details |
|------|------|---------|
| Create directory structure | 15m | `llm-wiki/`, `raw/`, `wiki/` with subdirectories, `llmwiki/` package |
| Initialize git repo | 5m | `git init`, `.gitignore` (exclude `config.yaml` with API key) |
| Set up Python environment | 15m | `python -m venv venv`, install `openai click python-frontmatter pyyaml rich` |
| Write `config.yaml` + `config.py` | 20m | API key, model name, base URL, wiki path, raw path |
| Write `llm.py` — API wrapper | 30m | Single function: `call_llm(system_prompt, user_prompt, model) → str`. Handles retries, token counting, error messages. |
| Write `cli.py` — skeleton | 20m | `click` CLI with `ingest`, `query`, `lint` subcommands (stubs that print "not implemented yet") |
| Test API connectivity | 10m | Simple test call to DeepSeek V3 via OpenRouter |
| Set up Obsidian | 20m | Install Obsidian, open `wiki/` as vault, install Dataview plugin, verify graph view |
| Create initial `index.md` and `log.md` | 15m | Empty templates with correct formatting |

**Deliverable:** Running `llmwiki --help` shows the CLI. Obsidian displays `index.md` and `log.md`. A test LLM call returns a response.

---

### Day 2 — Schema & Prompt Engineering (~3h)

**Goal:** The ingest prompt reliably produces well-structured wiki pages.

| Task | Time | Details |
|------|------|---------|
| Write prompt templates | 45m | Create files in `llmwiki/prompts/` — `ingest_summary.txt`, `ingest_update.txt`, `query.txt`, `lint.txt` based on Section 5 drafts |
| Write `wiki.py` — file helpers | 45m | Functions: `read_page(path)`, `write_page(path, content)`, `read_index()`, `update_index(entries)`, `append_log(entry)`, `list_pages()`, `parse_frontmatter(content)` |
| Gather 2-3 SQL source documents | 20m | Clip or save 2-3 beginner SQL articles/tutorials as markdown into `raw/`. These are your test fixtures. |
| Iterative prompt testing | 60m | Manually call `call_llm()` with the ingest prompt + a test source. Review output quality. Tweak prompt template until: frontmatter is correct, wikilinks are present, summary quality is good, page plan is reasonable. This is the most important iteration loop. |
| Decide on JSON vs plain text output | 10m | Test whether DeepSeek V3 reliably outputs valid JSON for the page plan. If not, switch to a simpler delimited format. |

**Deliverable:** The ingest prompt, given a source document and index, produces a correctly formatted summary page and a sensible plan for page updates/creations.

---

### Day 3 — Ingest Pipeline + File Watcher (~4h)

**Goal:** `llmwiki ingest <file>` works end-to-end. `llmwiki watch` monitors `raw/` for new files and auto-ingests them with desktop notifications.

| Task | Time | Details |
|------|------|---------|
| Implement `ingest.py` — Step 1 (summary + plan) | 45m | Read source file → read index → call LLM with `ingest_summary` prompt → parse JSON → write summary page to `wiki/source-summary/`. Includes defensive code-fence stripping. |
| Implement `ingest.py` — Step 2 (page create/update) | 45m | For each page in `pages_to_create`: load page template → call LLM with `ingest_update` prompt → strip outer fences → write page. For each page in `pages_to_update`: read existing page → call LLM → write updated page. All page writes flow through one `run_ingest()` function (single pipeline, no parallel write paths). |
| Implement index + log updates (hybrid approach) | 30m | Index update is **code-driven**: Python inserts new entries into the appropriate section of `index.md` using the `reason` field from the ingest plan as the one-line description. For updated pages, existing descriptions are preserved. Log update uses `wiki.append_log()` with pages_created, pages_updated, tokens_used. |
| Wire into CLI + `--all` flag | 20m | Connect `ingest.py` to `cli.py` `ingest` command with `rich` progress output. Add `--all` flag that detects un-ingested files in `raw/` (by comparing filenames against `log.md` entries) and ingests them sequentially. Add pre-command check that notifies user of pending files on any CLI invocation. |
| Build `watcher.py` + `llmwiki watch` command | 30m | File watcher using `watchdog` library (inotify-based, since `raw/` is on native WSL ext4). Detects new `.md` files in `raw/`, triggers `run_ingest()` automatically. Sends Windows desktop notification via PowerShell + BurntToast. Supports `--daemon` flag (forks to background with `start_new_session=True`, writes PID to `wiki/watcher.pid`, logs to `wiki/watcher.log`) and `--stop` flag (kills PID from file). |
| Install BurntToast + test notification | 5m | `powershell.exe -Command "Install-Module -Name BurntToast -Force -Scope CurrentUser"` — one-time setup. |
| Test with real source | 30m | Ingest `raw/postgres-joins.md` via CLI. Check in Obsidian: summary page exists, concept pages created, index updated, log entry appended, wikilinks work, graph view shows connections. Test watcher by dropping `raw/postgres-window-functions.md` and verifying auto-ingest + notification. |

**Key design decisions:**
- **Single pipeline function** (`run_ingest()`) — all operations (summary, page creates/updates, index, log) flow through one function. The watcher, `--all` flag, and single-file ingest all call this same function. Prevents the parallel-write-path drift flagged in yologdev's learnings.
- **Index updates are code-driven (hybrid)** — the LLM generates descriptions during ingest (via the `reason` field in the plan JSON), Python handles insertion into the right section of `index.md`. No extra LLM call, no risk of losing existing entries.
- **Defensive code-fence stripping** — `ingest_update` output is stripped of outer ``` fences before writing, in case the LLM regresses despite prompt instructions.

**Deliverable:** `llmwiki ingest raw/postgres-joins.md` produces wiki pages, updates index, writes log, renders in Obsidian. `llmwiki watch` auto-ingests new files dropped into `raw/` and sends a Windows toast notification.

---

### Day 4 — Ingest Hardening & Real Content (~3h)

**Goal:** Ingest pipeline handles edge cases; wiki has 3-5 ingested sources.

| Task | Time | Details |
|------|------|---------|
| Ingest 2-3 more sources | 60m | SQL tutorials/articles of varying length and complexity. Observe how the LLM handles updating existing pages when new sources overlap with old ones. |
| Fix prompt issues | 60m | Based on day 3-4 ingest results: fix formatting inconsistencies, improve cross-referencing, handle cases where LLM suggests updating a page that doesn't exist yet, handle long sources that exceed context window |
| Add context window management | 30m | If a source is too long, implement simple chunking: split source into sections, ingest each section with a note about which part it is. Not perfect, but sufficient for week 1. |
| Add `--dry-run` flag | 15m | Show what the LLM plans to do (page plan) without actually writing files. Useful for review before committing changes. |
| Git commit wiki state | 15m | Commit all wiki pages so far. This is your first "checkpoint." |

**Deliverable:** Wiki has 15-30 pages across 3-5 ingested sources. Obsidian graph view shows a connected knowledge structure. You can browse and follow wikilinks between concepts.

---

### Day 5 — Query Pipeline (~3h)

**Goal:** `llmwiki query "question"` returns good answers grounded in wiki content.

| Task | Time | Details |
|------|------|---------|
| Implement `query.py` — page selection | 45m | Read index → send to LLM with question → parse list of relevant page filenames |
| Implement `query.py` — synthesis | 45m | Read selected pages → send to LLM with pages + question → return synthesized answer |
| Implement `--save` flag | 30m | When passed, file the answer as a `wiki/note/` page with frontmatter (question, pages consulted, date). Update index. |
| Test with 5-10 questions | 45m | Test various query types: factual ("What is a LEFT JOIN?"), comparative ("When should I use a subquery vs a CTE?"), synthesis ("Summarize everything I know about joins"). Check answer quality and citation accuracy. |
| Handle "not enough info" case | 15m | When the wiki doesn't have enough information, the LLM should say so clearly and suggest what sources to add |

**Deliverable:** `llmwiki query "Explain window functions with examples" --save` prints a good answer to terminal and files it as a note in the wiki.

---

### Day 6 — Lint & CLI Polish (~3h)

**Goal:** Lint operation works. CLI is pleasant to use.

| Task | Time | Details |
|------|------|---------|
| Implement `lint.py` | 75m | Read all wiki pages → batch into LLM calls (if total content exceeds context, process in groups) → parse report → print structured output with `rich` |
| Add non-LLM lint checks | 30m | Broken wikilinks (link target doesn't exist), pages not in index, pages with missing frontmatter fields. These are deterministic — no LLM needed, fast, and reliable. |
| CLI improvements | 30m | Better error messages, `--verbose` flag for seeing LLM prompts/responses, `--model` override flag, help text for each command |
| Add `llmwiki status` command | 15m | Quick stats: total pages, pages by type, total sources ingested, last activity from log |
| Run full lint on current wiki | 30m | Fix any issues found. This is both testing the lint tool and improving your wiki. |

**Deliverable:** `llmwiki lint` produces a structured report. `llmwiki status` shows wiki stats. CLI has clear help text and error handling.

---

### Day 7 — End-to-End Testing & Documentation (~2h)

**Goal:** Everything works together. Another person could set this up from your README.

| Task | Time | Details |
|------|------|---------|
| Full end-to-end test | 45m | Fresh ingest of a new source → query about it → lint → verify everything is consistent. Test the full loop. |
| Write README.md | 30m | Setup instructions, usage examples for each command, configuration explanation. Concise — just enough for someone (or future you) to get running. |
| Final git commit + cleanup | 15m | Clean up any debug code, ensure `.gitignore` covers `config.yaml`, commit everything |
| Bug fixes | 30m | Buffer time for anything broken discovered during testing |

**Deliverable:** A complete, working `llmwiki` CLI with ingest, query, lint, and status commands. A wiki with real content visible in Obsidian. A README that explains how to use it.

---

### Day 8 — Watcher Hardening + Interactive Shell (~5-6h, two sessions)

**Goal:** Fully automatic ingest (no manual daemon start) + interactive REPL shell with chat mode.

**Session 1: Watcher Daemon Hardening (~2-2.5h)**

Makes the file watcher truly automatic — starts on Windows login, survives terminal closes and crashes, requires zero user intervention.

| Task | Time | Details |
|------|------|---------|
| Create `.wslconfig` | 5m | Add `vmIdleTimeout=-1` and `instanceIdleTimeout=-1` to `C:\Users\arjun\.wslconfig` to prevent WSL auto-shutdown when terminals are closed. |
| Create systemd user service | 20m | Write `~/.config/systemd/user/llm-wiki-watcher.service` with `Restart=always`, `RestartSec=5`, pointing at the venv Python and `watcher.py`. Enable with `systemctl --user enable llm-wiki-watcher` + `loginctl enable-linger arjun`. |
| Windows Task Scheduler entry | 15m | Create a logon-trigger task that runs `wsl.exe -d Ubuntu` to ensure WSL boots on Windows login. Systemd then auto-starts the watcher service. Use `schtasks.exe` from WSL or the Task Scheduler GUI. |
| PID file locking | 20m | Upgrade `watcher.py` from simple PID file to `fcntl.flock` — acquire an exclusive lock on `watcher.pid` at startup. If the lock is held, refuse to start (prevents double instances). If the lock is stale (process died), the OS releases it automatically. |
| Keep-alive tmux workaround | 10m | For WSL 2.5.7 suspension bug (Microsoft/WSL#13291): add a `.bashrc` snippet that starts a detached tmux session (`tmux new-session -d -s keepalive 2>/dev/null`) to prevent VM suspension. |
| **Test: systemd restart** | 15m | Start the service, kill the watcher process (`kill <pid>`), verify systemd restarts it within 5 seconds. Check `systemctl --user status llm-wiki-watcher`. |
| **Test: terminal close survival** | 10m | Start a terminal, verify watcher is running (`systemctl --user status`), close all terminals, reopen a terminal, verify watcher is still running. |
| **Test: full ingest cycle** | 15m | With watcher running via systemd (no manual start), drop a new `.md` file into `raw/` via Windows Explorer or Obsidian Web Clipper. Verify: file detected → ingest runs → pages created → desktop notification received → log.md updated. |

**Session 1 deliverable:** User logs into Windows → WSL boots → systemd starts watcher → files dropped in `raw/` are auto-ingested with desktop notifications. Zero manual intervention.

**Session 2: Interactive Shell (~3-3.5h)**

Adds `llmwiki shell` — a persistent REPL where natural language input is auto-routed to `query` and commands work with tab completion.

| Task | Time | Details |
|------|------|---------|
| Install `click-repl` | 5m | `pip install click-repl`, add to `requirements.txt`. Pulls in `prompt-toolkit` transitively. |
| Basic REPL integration | 20m | Use `register_repl(cli)` or custom shell command. Verify: shell opens, existing commands work with tab completion, Ctrl+D exits. |
| **Test: basic commands** | 10m | Inside the shell, run `status`, `lint --deterministic-only`, `ingest raw/<file> --dry-run`. Verify each works and output renders correctly with `rich`. |
| Chat mode wrapper | 45m | Custom dispatch logic: if first word isn't in `{ingest, query, lint, status, watch, help, exit}`, prepend `query` and route. Handle edge cases: quoted strings, empty input, Ctrl+C (continue, don't exit), multi-word questions. |
| **Test: chat mode** | 15m | Type plain questions: "What is a LEFT JOIN?", "explain window functions", "WHERE vs HAVING". Verify each routes to query and returns a grounded answer. Also test explicit `query "question" --save` still works. |
| Persistent history | 10m | Add `FileHistory(".llmwiki_history")` to the prompt session. Verify: type commands, exit shell, reopen shell, press up-arrow — previous commands appear. |
| **Test: history** | 5m | Open shell, run 3 commands, exit, reopen, press up-arrow 3 times. Verify all 3 recalled. |
| Welcome banner | 20m | On shell start: display a `rich` Panel with available commands, chat mode hint, keyboard shortcuts, and current wiki stats (from `page_stats()`). |
| `help` command | 15m | Show all commands with their flags and a one-line description. Include "Or just type any question directly" hint at the bottom. |
| **Test: UX polish** | 10m | Open shell — verify banner displays with correct stats. Run `help` — verify all commands listed. Press Tab on empty prompt — verify command list appears. Type `ingest [TAB]` — verify file paths complete. |
| Styled prompt | 10m | Custom prompt text (`llmwiki> `) with color via prompt_toolkit styles. |
| Wire into CLI + update docs | 15m | Add `shell` command to `cli.py`. Update README with shell usage. Update CLAUDE.md if needed. |
| **Test: end-to-end shell session** | 15m | Full workflow inside the shell: `status` → type a question (chat mode) → `ingest raw/<file>` → `lint --deterministic-only` → type another question → `exit`. Verify everything works without leaving the shell. |

**Session 2 deliverable:** `llmwiki shell` opens an interactive session with welcome banner, tab completion, persistent history, and chat mode (plain text auto-routed to query).

**Key design decisions:**
- **No slash commands.** Commands are bare words (`ingest`, `lint`, `status`). Anything that doesn't match a command is a query. This matches how users naturally think — questions don't need syntax.
- **Welcome banner shows state.** User immediately knows how big the wiki is and what commands are available.
- **Three discovery layers.** Welcome banner (seen once), `help` command (on demand), tab completion (muscle memory). User never has to memorize anything.
- **click-repl as foundation.** Auto-derives tab completion from Click commands. Chat mode added via ~20-line wrapper. Upgrade path to direct prompt_toolkit if needed later.

---

## 7. Risk & Fallback Notes

**Prompt iteration is the #1 time risk.** Getting the LLM to produce consistently well-formatted pages with correct wikilinks and frontmatter may take more iteration than estimated. Mitigation: days 2-4 have built-in iteration time, and the prompt templates in Section 5 give a concrete starting point.

**JSON output reliability.** DeepSeek V3 may not always produce valid JSON for the ingest page plan. Fallback: use a simpler format (one page per line with a delimiter) or add a JSON repair step.

**Context window limits.** Long sources (papers, long articles) may exceed the model's effective context when combined with existing wiki pages. Mitigation: the chunking approach on day 4 handles this adequately for week 1.

**Orphan [[wikilinks]] from `ingest_update.txt`.** Discovered during Day 2 prompt testing (2026-04-16). The `ingest_update.txt` prompt does NOT have access to the wiki index — it only sees the source excerpt and the page being created/updated. As a result, the LLM may add [[wikilinks]] to closely-related concepts that don't exist in the wiki and weren't planned by `ingest_summary.txt` (e.g., a window-functions page might link to [[Aggregate Functions]] when no such page exists). Current decision: ACCEPT this behavior. These orphan wikilinks represent legitimate concept gaps — they highlight where the wiki could be expanded next. The lint operation (Day 6) will detect and report them as "missing concept pages" with the count of how many other pages reference the same concept. Future fix (deferred): pass the index into `ingest_update.txt` so the LLM can self-check, accepting the higher token cost.

**If you're behind schedule, cut in this order:**
1. Lint (day 6) — defer entirely; ingest + query alone are a complete workflow
2. `--save` flag on query — just print answers, don't file them
3. `--dry-run` flag on ingest — nice-to-have, not essential
4. `rich` formatting — plain `print()` works fine

---

## 8. Future Work

Logical next steps after this week, in priority order:

1. **Context window management (chunking)** — Implement simple chunking for long sources that exceed the LLM's effective context window. Split source into sections, ingest each section with metadata noting which part it is and what the full source covers. Needed when ingesting long articles, papers, or book chapters (>15K tokens). Current sources are small enough that this hasn't been triggered, but it will be needed as the wiki grows. Implementation: add a `_chunk_source()` function in `ingest.py` that splits by markdown headings or by token count, then calls `run_ingest()` per chunk with a modified source filename suffix (e.g., `paper-part-1.md`).

2. **Confidence and lifecycle frontmatter** — Add `confidence: high|medium|low|stale` and `lifecycle: draft|reviewed|verified|stale|archived` fields to page frontmatter. Lint can flag pages that haven't been verified, that are stale (last_updated > 90 days with high confidence), or that need review. Low effort to add since `parse_frontmatter()` already accepts arbitrary fields — just extend the page templates and ingest prompt to populate them.

2. **Inline footnote citations** — Currently citations are tracked at the page level via the frontmatter `sources` list. For research-grade content, upgrade to inline footnote citations (`[^1]: source.pdf, p.3`) so individual claims can be attributed. Requires: a footnote-aware lint check (catches duplicate/skipped numbers) and updated ingest prompt to maintain footnote registry. Additive to current schema — does not break existing pages.

3. **Hub page (`overview.md`)** — Add a living synthesis page alongside `index.md` and `log.md`. Where the index is a flat catalog, `overview.md` would summarize what the wiki covers, list key findings across all sources, and show recent updates. Updated on every ingest. Useful when the wiki grows past ~50 pages and a flat index is no longer enough to give a sense of the wiki's shape.

4. **Local LLM via Ollama** — Set up Qwen 2.5 7B on the RTX 4060 laptop. Add hybrid routing to the CLI (local for quick queries, OpenRouter for ingest/lint). Reduces cost to near-zero for daily queries and enables offline use.

5. **Improved search at scale** — Once the wiki exceeds ~100 pages, `index.md` scanning becomes a bottleneck. Integrate `qmd` (local markdown search with BM25 + vector) or build a simple FAISS/BM25 index over page content.

6. **Batch ingest** — Ingest multiple sources in one command with less supervision. Useful for bootstrapping a new topic quickly.

7. **Web clipper integration** — Automate the Obsidian Web Clipper → `raw/` → `llmwiki ingest` pipeline so clipping an article triggers ingestion automatically.

8. **Notion sync (one-way)** — Push selected wiki pages to Notion for mobile reading. Obsidian remains the source of truth; Notion is a read-only mirror.

9. **Richer output formats** — Marp slide generation, matplotlib charts for data-heavy topics, Dataview queries in wiki pages.

10. ~~**Watcher daemon hardening**~~ — Moved to Day 8 (Session 1).
