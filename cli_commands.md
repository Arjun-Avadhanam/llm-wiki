  Full CLI reference

  llmwiki [--verbose] [--version] [--help] COMMAND

  ┌─────────────┬─────────────────────────────────────────────────────────────────────────────────┐
  │ Global flag │                                  What it does                                   │
  ├─────────────┼─────────────────────────────────────────────────────────────────────────────────┤
  │ --verbose   │ Prints the full LLM prompts and responses (useful for debugging prompt quality) │
  ├─────────────┼─────────────────────────────────────────────────────────────────────────────────┤
  │ --version   │ Shows version (0.1.0)                                                           │
  └─────────────┴─────────────────────────────────────────────────────────────────────────────────┘

  Commands

  ┌───────────────┬──────────────────────┬──────────────────────────────────────────────────────────────────────────────────────────────────┐
  │    Command    │        Flags         │                                           What it does                                           │
  ├───────────────┼──────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ ingest <file> │                      │ Ingest a single source file. LLM creates summary + concept pages, updates index and log.         │
  ├───────────────┼──────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ ingest <file> │ --dry-run            │ Show what the LLM would do (pages to create/update) without writing anything.                    │
  ├───────────────┼──────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ ingest        │ --all                │ Find all un-ingested .md files in raw/ and ingest them one by one.                               │
  ├───────────────┼──────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ query         │                      │ Ask the wiki a question. LLM picks relevant pages, synthesizes a grounded answer with citations. │
  │ "question"    │                      │                                                                                                  │
  ├───────────────┼──────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ query         │ --save               │ Same as above, but also saves the answer as a wiki/note/ page (explorations compound).           │
  │ "question"    │                      │                                                                                                  │
  ├───────────────┼──────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ lint          │                      │ Full health check — deterministic checks (instant) + LLM heuristic checks (contradictions, stale │
  │               │                      │  claims, missing concepts, weak cross-refs, recommended next topics).                            │
  ├───────────────┼──────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ lint          │ --deterministic-only │ Fast, free checks only — broken wikilinks, missing index entries, missing frontmatter,           │
  │               │                      │ root-level pages. No LLM call.                                                                   │
  ├───────────────┼──────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ status        │                      │ Show wiki stats: page counts by type, total sources, pending ingests.                            │
  ├───────────────┼──────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ watch         │                      │ Start file watcher in foreground. Monitors raw/ for new .md files, auto-ingests them, sends      │
  │               │                      │ desktop notifications. Ctrl+C to stop.                                                           │
  ├───────────────┼──────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ watch         │ --daemon             │ Start watcher in background. Writes PID to wiki/watcher.pid, logs to wiki/watcher.log.           │
  ├───────────────┼──────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ watch         │ --stop               │ Stop a running background watcher.                                                               │
  └───────────────┴──────────────────────┴──────────────────────────────────────────────────────────────────────────────────────────────────┘

  Pre-command notice: Every command automatically checks for un-ingested files in raw/ and prints a notice if any are pending.