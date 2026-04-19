# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

LLM Wiki — a Python CLI tool implementing Karpathy's LLM Wiki pattern. An LLM (DeepSeek V3 via OpenRouter) ingests source documents into a structured, interlinked markdown wiki viewable through Obsidian. Three core operations: ingest, query, lint.

## Running the CLI

```bash
cd /home/arjun/LLM_Wiki
source llm_wiki_venv/bin/activate
python3 -m llmwiki.cli [command]
```

Commands:
- `ingest <file>` / `ingest --all` / `ingest <file> --dry-run`
- `query "question"` / `query "question" --save`
- `lint` / `lint --deterministic-only`
- `status`
- `watch` / `watch --daemon` / `watch --stop`

Global flags: `--verbose` (prints LLM prompts/responses), `--version`

## Architecture

```
cli.py (Click entry point, pre-command pending source check)
  ├── ingest.py  → run_ingest()   — 2-step: summary+plan JSON, then per-page create/update
  ├── query.py   → run_query()    — 2-step: page selection JSON, then synthesis
  ├── lint.py    → run_lint()     — deterministic checks (Python) + LLM heuristic checks
  └── watcher.py → run_watcher()  — watchdog file monitor + BurntToast notifications

All modules use:
  ├── wiki.py   — single point of contact for ALL filesystem I/O (pages, index, log)
  ├── llm.py    — OpenRouter API wrapper (LLMResponse dataclass, retries, JSON validation)
  └── config.py — loads config.yaml, resolves paths, reads API key from file
```

**Critical design rule:** All page writes, index updates, and log appends flow through `wiki.py`. No module touches the filesystem directly.

## Filesystem Layout

```
/home/arjun/LLM_Wiki/           ← code repo (git)
├── llmwiki/                     ← Python package
│   ├── prompts/                 ← prompt templates loaded at runtime via .format()
│   │   └── page_templates/      ← per-type markdown templates (concept, source-summary, etc.)
├── wiki → symlink to /mnt/c/.../LLMWiki/wiki    ← LLM-generated pages (separate git repo)
├── raw  → symlink to /mnt/c/.../LLMWiki/raw     ← source documents (same git repo as wiki)
├── config.yaml                  ← API config (gitignored)
└── openrouter_key.txt           ← API key (gitignored)
```

Two git repos: this one (code) and `C:\Users\arjun\Documents\Coding Work\development\LLMWiki\` (content: raw + wiki).

## Key Patterns

**Single pipeline function per operation.** `run_ingest()`, `run_query()`, `run_lint()` are the sole entry points. The watcher, `--all` flag, and single-file CLI all call `run_ingest()`. No parallel write paths.

**Index updates are code-driven.** `wiki.insert_index_entries()` inserts deterministically using the LLM's `reason` field as description. The LLM never rewrites the index.

**Log format is code-driven.** `wiki.append_log()` constructs the `## [YYYY-MM-DD] operation | title` prefix. The LLM never writes to log.md.

**Prompt templates are the schema.** Files in `llmwiki/prompts/` are loaded at runtime and filled via `.format()`. They are the single source of truth for wiki conventions — do not duplicate their content into Python string literals.

**LLM output cleaning.** `_clean_llm_page_output()` in `ingest.py` strips outer code fences and prose preambles before frontmatter. Always applied before writing any LLM-generated page.

**Watcher auto-detects filesystem type.** `_make_observer()` resolves symlinks and uses `PollingObserver` for `/mnt/` paths (WSL drvfs limitation), `Observer` (inotify) for native ext4.

## Page Types and Frontmatter

Every wiki page requires: `title`, `type`, `tags`, `sources`, `created`, `updated`. Types: `concept`, `source-summary`, `comparison`, `reference`, `note`. Files use kebab-case names in type-named subdirectories.

## Dependencies

`openai`, `click`, `python-frontmatter`, `pyyaml`, `rich`, `watchdog` — listed in `requirements.txt`.

## Config

Model: `deepseek/deepseek-chat-v3-0324` via OpenRouter. Fallback: `qwen/qwen-2.5-72b-instruct`. API key read from `openrouter_key.txt` (never stored in config.yaml or code).
