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

import fcntl
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from rich.console import Console
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler

from llmwiki import wiki
from llmwiki.config import load_config


# Polling interval in seconds for PollingObserver on /mnt/c/ paths.
_POLL_INTERVAL = 3

console = Console()

# Path to the PID file for daemon mode
_PID_FILE = Path(load_config()["paths"]["wiki_dir"]) / "watcher.pid"

# File handle kept open for the lifetime of the process to hold the lock.
_lock_fh = None


def _acquire_lock() -> bool:
    """Acquire an exclusive file lock on watcher.pid to prevent double instances.

    Uses fcntl.flock which is automatically released when the process dies
    (no stale lock problem). If the lock is already held by another process,
    returns False.

    Returns:
        True if lock acquired, False if another watcher is already running.
    """
    global _lock_fh
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    _lock_fh = open(_PID_FILE, "w")
    try:
        fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fh.write(str(os.getpid()))
        _lock_fh.flush()
        return True
    except OSError:
        _lock_fh.close()
        _lock_fh = None
        return False


def _release_lock() -> None:
    """Release the file lock and clean up the PID file."""
    global _lock_fh
    if _lock_fh is not None:
        try:
            fcntl.flock(_lock_fh, fcntl.LOCK_UN)
            _lock_fh.close()
        except OSError:
            pass
        _lock_fh = None
    _PID_FILE.unlink(missing_ok=True)


def _make_observer(watch_path: str):
    """Create the appropriate observer for the given path.

    Uses PollingObserver for /mnt/ paths (Windows filesystem via WSL's
    drvfs driver, where inotify doesn't work for external changes).
    Uses the default inotify-based Observer for native ext4 paths.

    Args:
        watch_path: The directory path to watch.

    Returns:
        A watchdog Observer or PollingObserver instance.
    """
    # Resolve symlinks to get the real filesystem path.
    # e.g., /home/arjun/LLM_Wiki/raw → /mnt/c/Users/.../raw
    real_path = str(Path(watch_path).resolve())
    if real_path.startswith("/mnt/"):
        console.print(f"[dim]Using PollingObserver (interval={_POLL_INTERVAL}s) for /mnt/ path[/dim]")
        return PollingObserver(timeout=_POLL_INTERVAL)
    else:
        console.print("[dim]Using inotify Observer for native path[/dim]")
        return Observer()


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


import queue
import threading

# Queue for files waiting to be ingested. The event handler adds to it;
# the worker thread processes one at a time.
_ingest_queue: queue.Queue = queue.Queue()


def _ingest_worker():
    """Worker thread that processes the ingest queue sequentially.

    Runs in a daemon thread. Pulls file paths from the queue one at a
    time, ingests each, and sends notifications. This ensures multiple
    files clipped in quick succession are all processed, not lost.
    """
    from llmwiki.ingest import run_ingest

    while True:
        path = _ingest_queue.get()
        if path is None:
            break

        console.print(f"\n[bold cyan]Processing:[/bold cyan] {path.name}")
        _send_notification("LLM Wiki", f"Ingesting: {path.name}")

        try:
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
        finally:
            _ingest_queue.task_done()


class _NewSourceHandler(FileSystemEventHandler):
    """Watchdog event handler that queues new .md files for ingestion.

    Instead of processing inline (which blocks and causes missed files
    when multiple are added quickly), files are added to a queue. A
    worker thread processes them sequentially.
    """

    def __init__(self):
        super().__init__()
        self._seen: set[str] = set()

    def _enqueue_file(self, path: Path) -> None:
        """Add a file to the ingest queue if it's new and un-ingested."""
        if path.suffix != ".md":
            return

        # Deduplicate events for the same file (watchdog fires multiple)
        if path.name in self._seen:
            return
        self._seen.add(path.name)

        # Skip already-ingested files
        if path.name in wiki.get_ingested_sources():
            return

        console.print(f"\n[bold cyan]New file detected:[/bold cyan] {path.name} (queued)")
        _ingest_queue.put(path)

    def on_created(self, event):
        """Called when a new file is created in the watched directory."""
        if not event.is_directory:
            self._enqueue_file(Path(event.src_path))

    def on_moved(self, event):
        """Called when a file is moved/renamed into the watched directory.

        Some tools (editors, Claude Code's Write tool) use atomic writes:
        write to a temp file, then rename into place. This generates a
        'moved' event instead of 'created'.
        """
        if not event.is_directory:
            self._enqueue_file(Path(event.dest_path))

    def on_closed(self, event):
        """Called when a file is closed after writing (Linux only).

        Catches cases where a file is created and then written to
        in separate steps — the 'closed' event fires after the write
        completes, ensuring we read the full content.
        """
        if not event.is_directory:
            self._enqueue_file(Path(event.src_path))


def _startup_scan():
    """Check for un-ingested files on startup.

    Catches files added while the watcher was not running (e.g.,
    laptop was off, WSL was shut down). Any pending files are
    queued for ingestion before the watch loop begins.
    """
    pending = wiki.get_pending_sources()
    if pending:
        console.print(f"[yellow]Startup scan: {len(pending)} un-ingested file(s) found[/yellow]")
        for p in pending:
            console.print(f"  Queuing: {p.name}")
            _ingest_queue.put(p)
    else:
        console.print("[dim]Startup scan: all sources up to date[/dim]")


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
    if not _acquire_lock():
        console.print("[red]Another watcher is already running. Use 'llmwiki watch --stop' first.[/red]")
        return

    console.print(f"[bold]Watching {raw_dir} for new files...[/bold]")
    console.print("[dim]Press Ctrl+C to stop.[/dim]\n")

    # Start the worker thread that processes the ingest queue
    worker = threading.Thread(target=_ingest_worker, daemon=True)
    worker.start()

    # Scan for files missed while the watcher was not running
    _startup_scan()

    observer = _make_observer(raw_dir)
    observer.schedule(_NewSourceHandler(), raw_dir, recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping watcher...[/yellow]")
        observer.stop()
        _ingest_queue.put(None)  # Signal worker to exit

    observer.join()
    worker.join(timeout=5)
    _release_lock()
    console.print("[green]Watcher stopped.[/green]")


def stop_watcher() -> None:
    """Stop a running watcher (daemon or systemd service).

    Tries two methods:
    1. Read PID from watcher.pid and send SIGTERM (for daemon mode)
    2. Stop the systemd user service (for service mode)
    """
    stopped = False

    # Method 1: PID file
    if _PID_FILE.exists():
        try:
            pid = int(_PID_FILE.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            console.print(f"[green]Watcher (PID {pid}) stopped.[/green]")
            stopped = True
        except (ProcessLookupError, ValueError):
            console.print(f"[yellow]Stale PID file — cleaning up.[/yellow]")
        _PID_FILE.unlink(missing_ok=True)

    # Method 2: systemd service
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "llm-wiki-watcher"],
            capture_output=True, text=True,
        )
        if result.stdout.strip() == "active":
            subprocess.run(
                ["systemctl", "--user", "stop", "llm-wiki-watcher"],
                check=True,
            )
            console.print("[green]Stopped systemd llm-wiki-watcher service.[/green]")
            stopped = True
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    if not stopped:
        console.print("[yellow]No running watcher found.[/yellow]")


# Allow running as `python -m llmwiki.watcher <raw_dir>` for daemon/systemd mode.
if __name__ == "__main__":
    if len(sys.argv) > 1:
        raw_path = sys.argv[1]

        if not _acquire_lock():
            print("Another watcher is already running. Exiting.")
            sys.exit(1)

        worker = threading.Thread(target=_ingest_worker, daemon=True)
        worker.start()

        _startup_scan()

        observer = _make_observer(raw_path)
        observer.schedule(_NewSourceHandler(), raw_path, recursive=False)
        observer.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
            _ingest_queue.put(None)
        observer.join()
        worker.join(timeout=5)
        _release_lock()
    else:
        run_watcher(daemon=False)
