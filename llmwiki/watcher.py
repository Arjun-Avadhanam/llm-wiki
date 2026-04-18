"""File watcher for automatic source ingestion.

Monitors the raw/ directory for new .md files using inotify (via the
watchdog library). When a new file is detected, it is automatically
ingested into the wiki and a Windows desktop notification is sent via
PowerShell + BurntToast.

This works reliably because raw/ is on the native WSL ext4 filesystem,
where inotify is fully supported. (The /mnt/c/ inotify limitation
does NOT affect this watcher.)

Supports two modes:
    - Foreground: `llmwiki watch` — runs in the terminal, Ctrl+C to stop.
    - Daemon: `llmwiki watch --daemon` — forks to background, writes PID
      to wiki/watcher.pid, logs to wiki/watcher.log. Stop with
      `llmwiki watch --stop`.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from rich.console import Console
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from llmwiki import wiki
from llmwiki.config import load_config

console = Console()

# Path to the PID file for daemon mode
_PID_FILE = Path(load_config()["paths"]["wiki_dir"]) / "watcher.pid"


def _send_notification(title: str, message: str) -> None:
    """Send a Windows desktop notification via PowerShell + BurntToast.

    Runs asynchronously (fire-and-forget) so it doesn't block the
    watcher. If BurntToast is not installed or PowerShell is unavailable,
    the notification silently fails — this is intentional, as
    notifications are a convenience, not a requirement.

    Args:
        title: Toast notification title.
        message: Toast notification body text.
    """
    try:
        # Use -AppLogo with an empty string to suppress the PowerShell
        # icon, and set -UniqueIdentifier so updates replace previous
        # toasts instead of stacking. The notification is informational
        # only — clicking it opens PowerShell (a WSL/BurntToast
        # limitation), so we make the toast body rich enough that the
        # user doesn't need to click through.
        ps_cmd = (
            f"New-BurntToastNotification "
            f"-Text '{title}','{message}' "
            f"-UniqueIdentifier 'llmwiki'"
        )
        subprocess.Popen(
            ["powershell.exe", "-Command", ps_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        # PowerShell not available (not on WSL, or not in PATH).
        pass


class _NewSourceHandler(FileSystemEventHandler):
    """Watchdog event handler that triggers ingest on new .md files.

    Tracks which files have been processed in-memory to avoid
    double-processing from duplicate events (watchdog can fire both
    created and modified events for the same file).
    """

    def __init__(self):
        super().__init__()
        self._processed: set[str] = set()

    def _handle_new_file(self, path: Path) -> None:
        """Process a new source file — shared logic for all event types."""
        if path.suffix != ".md":
            return

        # Avoid double-processing (watchdog can fire multiple events
        # for the same file: created + modified, or moved + modified)
        if path.name in self._processed:
            return
        self._processed.add(path.name)

        # Check if already ingested
        if path.name in wiki.get_ingested_sources():
            return

        console.print(f"\n[bold cyan]New file detected:[/bold cyan] {path.name}")
        _send_notification("LLM Wiki", f"Ingesting: {path.name}")

        try:
            from llmwiki.ingest import run_ingest
            result = run_ingest(path)
            created = len(result['pages_created'])
            updated = len(result['pages_updated'])
            _send_notification(
                "LLM Wiki — Ingest Complete",
                f"{path.name}: {created} created, {updated} updated, {result['total_tokens']} tokens",
            )
        except Exception as e:
            console.print(f"[red]Ingest failed for {path.name}: {e}[/red]")
            _send_notification("LLM Wiki", f"Ingest failed: {path.name}")

    def on_created(self, event):
        """Called when a new file is created in the watched directory."""
        if not event.is_directory:
            self._handle_new_file(Path(event.src_path))

    def on_moved(self, event):
        """Called when a file is moved/renamed into the watched directory.

        Some tools (editors, Claude Code's Write tool) use atomic writes:
        write to a temp file, then rename into place. This generates a
        'moved' event instead of 'created'.
        """
        if not event.is_directory:
            self._handle_new_file(Path(event.dest_path))

    def on_closed(self, event):
        """Called when a file is closed after writing (Linux only).

        Catches cases where a file is created and then written to
        in separate steps — the 'closed' event fires after the write
        completes, ensuring we read the full content.
        """
        if not event.is_directory:
            self._handle_new_file(Path(event.src_path))


def run_watcher(daemon: bool = False) -> None:
    """Start the file watcher on the raw/ directory.

    Args:
        daemon: If True, fork to background and write PID to file.
                If False, run in the foreground (blocking).
    """
    raw_dir = str(wiki.get_raw_dir())

    if daemon:
        # Fork to background
        log_file = Path(load_config()["paths"]["wiki_dir"]) / "watcher.log"
        console.print(f"Starting watcher daemon...")
        console.print(f"  Watching: {raw_dir}")
        console.print(f"  Log: {log_file}")
        console.print(f"  PID file: {_PID_FILE}")

        # Launch a new process running this same script in foreground mode,
        # with stdout/stderr redirected to the log file.
        process = subprocess.Popen(
            [
                sys.executable,
                "-m", "llmwiki.watcher",
                raw_dir,
            ],
            stdout=open(log_file, "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        _PID_FILE.write_text(str(process.pid))
        console.print(f"  PID: {process.pid}")
        console.print("[green]Watcher started in background.[/green]")
        return

    # Foreground mode
    console.print(f"[bold]Watching {raw_dir} for new files...[/bold]")
    console.print("[dim]Press Ctrl+C to stop.[/dim]\n")

    observer = Observer()
    observer.schedule(_NewSourceHandler(), raw_dir, recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping watcher...[/yellow]")
        observer.stop()

    observer.join()
    console.print("[green]Watcher stopped.[/green]")


def stop_watcher() -> None:
    """Stop a running background watcher by reading its PID file."""
    if not _PID_FILE.exists():
        console.print("[yellow]No watcher PID file found — is it running?[/yellow]")
        return

    pid = int(_PID_FILE.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        console.print(f"[green]Watcher (PID {pid}) stopped.[/green]")
    except ProcessLookupError:
        console.print(f"[yellow]Process {pid} not found — stale PID file.[/yellow]")

    _PID_FILE.unlink(missing_ok=True)


# Allow running as `python -m llmwiki.watcher <raw_dir>` for daemon mode.
if __name__ == "__main__":
    if len(sys.argv) > 1:
        raw_path = sys.argv[1]
        # Override the raw dir for this subprocess
        observer = Observer()
        observer.schedule(_NewSourceHandler(), raw_path, recursive=False)
        observer.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
        observer.join()
    else:
        run_watcher(daemon=False)
