# LLM Wiki

A Python CLI tool that implements [Andrej Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — an LLM-maintained personal knowledge base of interlinked markdown files, viewable through Obsidian.

Instead of RAG (re-deriving knowledge from scratch on every query), the LLM **incrementally builds and maintains a persistent wiki**. When you add a source, the LLM reads it, extracts concepts, creates/updates wiki pages, maintains cross-references, and keeps everything consistent. Knowledge compounds over time.

## How It Works

```
User                                 LLM Wiki CLI                        Obsidian
  │                                      │                                  │
  ├─ Drop source into raw/ ────────────► │                                  │
  │                                      ├─ LLM reads source               │
  │                                      ├─ Creates summary page            │
  │                                      ├─ Creates/updates concept pages   │
  │                                      ├─ Updates index + log ───────────►│ Live reload
  │                                      │                                  │
  ├─ Ask a question ───────────────────► │                                  │
  │                                      ├─ Selects relevant pages          │
  │                                      ├─ Synthesizes answer              │
  │  ◄── Grounded answer with citations ─┤                                  │
  │                                      │                                  │
  ├─ Run lint ─────────────────────────► │                                  │
  │  ◄── Health report + recommendations ┤                                  │
```

## Setup

### Prerequisites

- Python 3.10+
- [Obsidian](https://obsidian.md/) (free)
- [OpenRouter](https://openrouter.ai/) account with API credit (~$5 lasts months)

### Installation

```bash
git clone <repo-url> && cd LLM_Wiki
python3 -m venv llm_wiki_venv
source llm_wiki_venv/bin/activate
pip install -r requirements.txt
```

### Configuration

1. Create `openrouter_key.txt` with your OpenRouter API key:
   ```bash
   echo "sk-or-v1-your-key-here" > openrouter_key.txt
   ```

2. Create `config.yaml`:
   ```yaml
   api:
     base_url: "https://openrouter.ai/api/v1"
     key_file: "openrouter_key.txt"
     model: "deepseek/deepseek-chat-v3-0324"
     fallback_model: "qwen/qwen-2.5-72b-instruct"
     max_tokens: 4096
     temperature: 0.3

   paths:
     wiki_dir: "wiki"
     raw_dir: "raw"
     prompts_dir: "llmwiki/prompts"
   ```

3. Create the wiki and raw directories:
   ```bash
   mkdir -p raw/assets wiki/concept wiki/source-summary wiki/comparison wiki/reference wiki/note
   ```

4. Create initial `wiki/index.md`:
   ```markdown
   # Wiki Index

   ## Concepts

   ## Source Summaries

   ## Comparisons

   ## References

   ## Notes
   ```

5. Create initial `wiki/log.md`:
   ```markdown
   # Wiki Log
   ```

6. Open the project root (or `wiki/`) as an Obsidian vault. Install the **Dataview** community plugin.

   **WSL note:** Obsidian (Windows) cannot watch files on the WSL2 filesystem (`\\wsl$\...`) due to missing `inotify` support (see [Microsoft/WSL#4739](https://github.com/microsoft/WSL/issues/4739)). To work around this, keep `wiki/` and `raw/` on the Windows filesystem (e.g., `C:\Users\<you>\Documents\LLMWiki\`) and create symlinks from the WSL project directory:
   ```bash
   ln -s "/mnt/c/Users/<you>/Documents/LLMWiki/wiki" wiki
   ln -s "/mnt/c/Users/<you>/Documents/LLMWiki/raw" raw
   ```
   Obsidian then opens the Windows-side directory as the vault, and the CLI accesses the same files through the symlinks.

## Usage

```bash
source llm_wiki_venv/bin/activate
```

### Ingest a source

Drop a markdown file into `raw/`, then:

```bash
python3 -m llmwiki.cli ingest raw/my-article.md
```

The LLM reads the source, creates a summary page, creates/updates concept pages, updates the index, and logs the operation. A single source typically touches 3-12 wiki pages.

```bash
# Preview what would happen without writing files
python3 -m llmwiki.cli ingest raw/my-article.md --dry-run

# Ingest all un-ingested files in raw/
python3 -m llmwiki.cli ingest --all
```

### Query the wiki

```bash
python3 -m llmwiki.cli query "What is the difference between WHERE and HAVING?"
```

The LLM selects relevant pages from the index, reads them, and synthesizes a grounded answer with `[[wikilink]]` citations.

```bash
# Save the answer as a wiki note (explorations compound)
python3 -m llmwiki.cli query "Explain window functions with examples" --save
```

### Lint the wiki

```bash
# Full lint (deterministic + LLM-based checks)
python3 -m llmwiki.cli lint

# Fast, free deterministic checks only
python3 -m llmwiki.cli lint --deterministic-only
```

**Deterministic checks:** broken wikilinks, pages missing from index, missing frontmatter fields, root-level pages.

**LLM checks:** contradictions, stale claims, missing concept pages, weak cross-references, recommended next topics to ingest.

### Watch for new sources (auto-ingest)

```bash
# Foreground (Ctrl+C to stop)
python3 -m llmwiki.cli watch

# Background daemon
python3 -m llmwiki.cli watch --daemon
python3 -m llmwiki.cli watch --stop
```

Monitors `raw/` for new `.md` files and auto-ingests them. Sends Windows desktop notifications via BurntToast (WSL only).

### Interactive shell

```bash
python3 -m llmwiki.cli shell
```

Opens a persistent session with tab completion and command history. Type commands normally, or just type a question in natural language — it auto-routes to `query`:

```
llmwiki> status
llmwiki> What is the difference between WHERE and HAVING?
llmwiki> lint --deterministic-only
llmwiki> explain window functions
llmwiki> exit
```

### Check status

```bash
python3 -m llmwiki.cli status
```

Shows page counts by type, source counts, and pending ingests.

### Debug mode

```bash
python3 -m llmwiki.cli --verbose query "my question"
```

Prints the full LLM prompts and responses for debugging.

## Wiki Structure

```
wiki/
├── index.md              ← Table of contents (auto-maintained)
├── log.md                ← Chronological operation log (append-only)
├── concept/              ← Core knowledge topics
├── source-summary/       ← One page per ingested source
├── comparison/           ← A vs B decision frameworks
├── reference/            ← Quick-lookup cheatsheets
└── note/                 ← Saved query answers, ad-hoc insights
```

Every page has YAML frontmatter: `title`, `type`, `tags`, `sources`, `created`, `updated`.

## Version control

This repo contains only the **code** (CLI tool, prompts, templates). Your wiki content (`wiki/` and `raw/`) should be tracked in a separate git repo — this keeps code and content independent.

To set up version control for your content:

```bash
cd /path/to/your/LLMWiki   # the directory containing raw/ and wiki/
git init
git branch -m main

echo ".obsidian/
raw/assets/
watcher.pid
watcher.log" > .gitignore

git add raw/ wiki/ .gitignore
git commit -m "initial wiki content"
```

You can then commit after each ingest session or periodically to checkpoint your wiki's evolution. The git history gives you a full timeline of how your knowledge base grew.

## Cost

Using DeepSeek V3 via OpenRouter:
- ~$0.001 per source ingest
- ~$0.001 per query
- ~$0.004 per full lint
- Typical monthly cost with daily usage: **$1-3**

## Architecture

See [CLAUDE.md](CLAUDE.md) for detailed architecture documentation.
