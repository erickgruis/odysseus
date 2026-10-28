"""Tests for Cookbook tmux session launching (stale-server cwd hardening)."""

import os
import subprocess

from src import tmux_session


def _run(cmd, rc=0, out=""):
    return subprocess.CompletedProcess(cmd, rc, out, "")


def test_launch_cwd_prefers_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ODYSSEUS_TMUX_CWD", str(tmp_path))
    assert tmux_session.tmux_launch_cwd() == str(tmp_path)


def test_launch_cwd_skips_missing_dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("ODYSSEUS_TMUX_CWD", str(tmp_path / "gone"))
    assert os.path.isdir(tmux_session.tmux_launch_cwd())


def test_new_session_command_cds_before_running(monkeypatch, tmp_path):
    monkeypatch.setenv("ODYSSEUS_TMUX_CWD", str(tmp_path))
    monkeypatch.setattr(tmux_session, "tmux_server_cwd_is_stale", lambda: False)

    cmd = tmux_session.tmux_new_session_command("serve-abc123", "/tmp/runner.sh")

    assert cmd.startswith("tmux new-session -d -s serve-abc123")
    assert f"-c {tmp_path}" in cmd
    # The pane must leave the inherited cwd before running the target, otherwise
    # a stale tmux server leaves pip in an unlinked directory and getcwd fails.
    assert f"cd {tmp_path} && exec /tmp/runner.sh" in cmd


def test_new_session_command_history_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("ODYSSEUS_TMUX_CWD", str(tmp_path))
    monkeypatch.setattr(tmux_session, "tmux_server_cwd_is_stale", lambda: False)

    cmd = tmux_session.tmux_new_session_command(
        "serve-abc123", "/tmp/runner.sh", history_limit=True
    )

    assert cmd.startswith("tmux set-option -g history-limit 100000")
    assert "new-session -d -s serve-abc123" in cmd


def test_new_session_command_quotes_space_paths(monkeypatch, tmp_path):
    spaced = tmp_path / "with space"
    spaced.mkdir()
    monkeypatch.setenv("ODYSSEUS_TMUX_CWD", str(spaced))
    monkeypatch.setattr(tmux_session, "tmux_server_cwd_is_stale", lambda: False)

    cmd = tmux_session.tmux_new_session_command("serve-abc123", "/tmp/runner.sh")

    assert "'" + str(spaced) + "'" in cmd


def test_server_cwd_missing_detects_unlinked_dir():
    # No such process -> /proc/<pid>/cwd cannot be stat'ed, i.e. cwd is gone.
    assert tmux_session._server_cwd_missing(2**22 + 12345) is True


def test_server_cwd_missing_false_for_live_dir():
    assert tmux_session._server_cwd_missing(os.getpid()) is False


def test_stale_detection_false_without_server(monkeypatch):
    monkeypatch.setattr(tmux_session, "_server_pid", lambda: None)
    assert tmux_session.tmux_server_cwd_is_stale() is False


def test_stale_detection_reports_deleted_cwd(monkeypatch):
    monkeypatch.setattr(tmux_session, "_server_pid", lambda: 4242)
    monkeypatch.setattr(tmux_session, "_server_cwd_missing", lambda pid: True)
    assert tmux_session.tmux_server_cwd_is_stale() is True
