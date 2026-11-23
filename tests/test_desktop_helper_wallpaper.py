"""Tests for the desktop helper's wallpaper and update verbs.

These tests do not touch a real desktop. They exercise backend detection,
argument validation, command construction, state/undo, and update parsing.
"""

from types import SimpleNamespace

import pytest

from desktop_helper import updates, wallpaper


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    yield


def _image(tmp_path):
    path = tmp_path / "wallpaper.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    return str(path)


def test_normalize_mode():
    assert wallpaper.normalize_mode("FILL") == "fill"
    assert wallpaper.normalize_mode("nonsense") == "fill"
    assert wallpaper.normalize_mode(None) == "fill"


def test_detect_backend_gnome(monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.setattr(wallpaper, "_which", lambda name: f"/usr/bin/{name}")
    assert wallpaper.detect_backend().kind == "gnome"


def test_detect_backend_kde(monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    monkeypatch.setattr(wallpaper, "_which", lambda name: f"/usr/bin/{name}")
    assert wallpaper.detect_backend().kind == "kde"


def test_detect_backend_niri_uses_swaybg(monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "niri")
    monkeypatch.setattr(
        wallpaper,
        "_which",
        lambda name: "/usr/bin/swaybg" if name == "swaybg" else None,
    )
    monkeypatch.setattr(wallpaper, "_swww_running", lambda: False)
    assert wallpaper.detect_backend().kind == "swaybg"


def test_detect_backend_unsupported(monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "niri")
    monkeypatch.setattr(wallpaper, "_which", lambda name: None)
    monkeypatch.setattr(wallpaper, "_swww_running", lambda: False)
    backend = wallpaper.detect_backend()
    assert backend.kind == "unsupported"
    assert "swaybg" in backend.detail


def test_set_wallpaper_requires_target():
    with pytest.raises(wallpaper.WallpaperError):
        wallpaper.set_wallpaper()


def test_set_wallpaper_rejects_missing_image(tmp_path):
    with pytest.raises(wallpaper.WallpaperError):
        wallpaper.set_wallpaper(image=str(tmp_path / "missing.png"))


def test_set_wallpaper_rejects_both_image_and_color(tmp_path):
    with pytest.raises(wallpaper.WallpaperError):
        wallpaper.set_wallpaper(image=_image(tmp_path), color="#101014")


def test_set_wallpaper_rejects_invalid_color():
    with pytest.raises(wallpaper.WallpaperError):
        wallpaper.set_wallpaper(color="not-a-color")


def test_swaybg_dry_run_reports_command(tmp_path, monkeypatch):
    monkeypatch.setattr(
        wallpaper, "detect_backend", lambda: wallpaper.Backend("swaybg", "swaybg")
    )
    monkeypatch.setattr(wallpaper, "_which", lambda name: "/usr/bin/swaybg")
    image = _image(tmp_path)

    result = wallpaper.set_wallpaper(image=image, mode="fill", dry_run=True)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["changed"] is False
    assert any("swaybg" in command for command in result["commands"])
    assert wallpaper.load_state() == {}


def test_swaybg_set_and_undo(tmp_path, monkeypatch):
    monkeypatch.setattr(
        wallpaper, "detect_backend", lambda: wallpaper.Backend("swaybg", "swaybg")
    )
    monkeypatch.setattr(wallpaper, "_which", lambda name: "/usr/bin/swaybg")
    terminated = []
    monkeypatch.setattr(wallpaper, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(wallpaper, "_terminate", lambda pid: terminated.append(pid))

    class FakePopen:
        def __init__(self, command, **kwargs):
            self.pid = 4242
            self.command = command

    monkeypatch.setattr(wallpaper.subprocess, "Popen", FakePopen)
    image = _image(tmp_path)

    result = wallpaper.set_wallpaper(image=image, mode="fill")
    assert result["ok"] is True
    state = wallpaper.load_state()
    assert state["backend"] == "swaybg"
    assert state["pid"] == 4242

    undone = wallpaper.set_wallpaper(undo=True)
    assert undone["ok"] is True
    assert terminated == [4242]
    assert wallpaper.load_state() == {}


def test_undo_without_state_fails():
    with pytest.raises(wallpaper.WallpaperError):
        wallpaper.set_wallpaper(undo=True)


def test_gnome_commands_and_undo(tmp_path, monkeypatch):
    monkeypatch.setattr(
        wallpaper, "detect_backend", lambda: wallpaper.Backend("gnome", "GNOME")
    )
    monkeypatch.setattr(wallpaper, "_gsettings_get", lambda schema, key: "old-value")
    commands = []
    monkeypatch.setattr(
        wallpaper,
        "_run_checked",
        lambda cmd: commands.append(cmd) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    image = _image(tmp_path)

    result = wallpaper.set_wallpaper(image=image, mode="fit")
    assert result["ok"] is True
    assert result["backend"] == "gnome"
    assert any(cmd[:2] == ["gsettings", "set"] for cmd in commands)
    assert any("picture-options" in cmd for cmd in commands)
    assert wallpaper.load_state()["previous"]["picture_uri"] == "old-value"

    commands.clear()
    undone = wallpaper.set_wallpaper(undo=True)
    assert undone["ok"] is True
    assert commands
    assert wallpaper.load_state() == {}


def test_check_updates_arch(monkeypatch):
    monkeypatch.setattr(
        updates, "_which", lambda name: "/usr/bin/pacman" if name == "pacman" else None
    )
    monkeypatch.setattr(
        updates,
        "_run",
        lambda cmd: SimpleNamespace(
            stdout="linux 6.1 -> 6.2\nfirefox 120 -> 121\n", stderr="", returncode=0
        ),
    )

    result = updates.check_updates()
    assert result["ok"] is True
    assert result["manager"] == "pacman"
    assert result["count"] == 2
    assert "linux" in result["packages"][0]


def test_check_updates_refresh_rejected():
    result = updates.check_updates(refresh=True)
    assert result["ok"] is False
    assert "software manager" in result["error"]
