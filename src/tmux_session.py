"""Helpers for launching Cookbook tmux sessions safely.

tmux binds the working directory when the tmux *server* starts and reuses it
for every pane that server later spawns. ``tmux new-session -c DIR`` does not
rescue a server whose own cwd inode has been unlinked (for example after the
install directory was replaced by an update): the pane still starts inside the
deleted directory, so ``getcwd()`` fails and tools abort with
``FileNotFoundError`` / ``std::env::current_dir() failed`` before they can run.

The fix is twofold and deliberately non-destructive:

* always pass an existing ``-c`` directory so fresh servers start in a sane
  place, and
* always prefix the session command with ``cd <dir> && exec <target>`` so the
  pane leaves a dead cwd before running anything, even when ``-c`` is ignored.

We never kill a stale server on our own: tmux servers are shared with the
user's own interactive sessions, and killing one would take down any model
server still running under it.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_DELETED_SUFFIX = " (deleted)"


def _app_root() -> Path:
    """Best-effort app root, falling back to the process cwd."""
    try:
        from src.constants import get_app_root

        root = Path(get_app_root())
        if root.is_dir():
            return root
    except Exception:  # pragma: no cover - defensive, import cycle safety
        pass
    try:
        return Path.cwd()
    except OSError:
        return Path("/")


def tmux_launch_cwd() -> str:
    """Return an existing directory to hand to ``tmux new-session -c``.

    Preference order: explicit override, the app root (so Cookbook commands
    resolve relative paths like ``scripts/``), the user's home, then ``/``.
    """
    candidates = (
        os.environ.get("ODYSSEUS_TMUX_CWD"),
        str(_app_root()),
        str(Path.home()),
        "/",
    )
    for candidate in candidates:
        if not candidate:
            continue
        try:
            path = Path(candidate).expanduser()
            if path.is_dir():
                return str(path)
        except OSError:
            continue
    return "/"


def _tmux(args: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["tmux", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _server_pid() -> int | None:
    proc = _tmux(["display-message", "-p", "#{pid}"])
    if proc is None or proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


def _server_cwd_missing(pid: int) -> bool:
    """True when ``/proc/<pid>/cwd`` no longer resolves (deleted directory)."""
    if Path("/proc").is_dir():
        try:
            os.stat(f"/proc/{pid}/cwd")
            return False
        except OSError:
            return True

    # Non-Linux fallback: tmux annotates unlinked pane cwds with "(deleted)" on
    # Linux; elsewhere this simply reports False rather than guessing.
    panes = _tmux(["list-panes", "-a", "-F", "#{pane_current_path}"])
    if panes is None or panes.returncode != 0:
        return False
    return any(
        line.strip().endswith(_DELETED_SUFFIX)
        for line in panes.stdout.splitlines()
        if line.strip()
    )


def tmux_server_cwd_is_stale() -> bool:
    """True when the running tmux server's own cwd no longer exists.

    Returns False when no server is running (the next launch starts fresh).
    Used for diagnostics; launch sessions use :func:`tmux_new_session_command`,
    which works around a stale server instead of restarting it.
    """
    pid = _server_pid()
    if pid is None:
        return False
    return _server_cwd_missing(pid)


def tmux_new_session_command(
    session_id: str,
    target: str,
    *,
    cwd: str | None = None,
    history_limit: bool = False,
) -> str:
    """Build a ``tmux new-session`` command that cannot inherit a dead cwd.

    ``target`` is the runner script (or command) the pane should execute. It is
    launched as ``cd <cwd> && exec <target>`` inside the pane so a stale tmux
    server cannot leave the process in an unlinked directory.
    """
    workdir = cwd or tmux_launch_cwd()
    quoted_dir = shlex.quote(workdir)
    inner = f"cd {quoted_dir} && exec {shlex.quote(str(target))}"
    prefix = (
        "tmux set-option -g history-limit 100000 2>/dev/null; "
        if history_limit
        else ""
    )
    if tmux_server_cwd_is_stale():
        logger.warning(
            "tmux server has a deleted working directory; launching %s from %s "
            "instead (server left running for existing sessions)",
            session_id,
            workdir,
        )
    return (
        f"{prefix}tmux new-session -d -s {shlex.quote(str(session_id))} "
        f"-c {quoted_dir} {shlex.quote(inner)}"
    )
