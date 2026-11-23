"""Wallpaper backends for the Odysseus desktop helper.

The helper exposes ``set_wallpaper`` as a local MCP tool. This module does the
work and keeps a small state file so the change can be undone.

Supported backends, chosen by desktop session and available binaries:

- GNOME (``gsettings``)
- KDE Plasma (``plasma-apply-wallpaperimage``)
- XFCE (``xfconf-query``)
- Dux Modular Shell (``dms ipc call wallpaper``), including per-monitor mode
- Wayland compositors with a running ``swww`` daemon
- Wayland compositors with ``swaybg`` (started as a layer-shell client)

DMS is preferred when it is running, because it owns the background layer.
Setting ``swaybg`` on top of DMS would paint over the shell's wallpaper and
leave DMS's own settings out of sync. The DMS backend changes the shell's
wallpaper directly, so the shell's settings stay consistent.

Unsupported environments return a clear error instead of guessing. Every
mutation records the previous state so ``undo`` can restore it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUPPORTED_MODES = ("fill", "fit", "stretch", "center", "tile")
_DEFAULT_MODE = "fill"
_TIMEOUT_S = 15

_GNOME_MODE = {
    "fill": "zoom",
    "fit": "scaled",
    "stretch": "stretched",
    "center": "centered",
    "tile": "wallpaper",
}


class WallpaperError(RuntimeError):
    """A wallpaper request that cannot be honored."""


@dataclass(frozen=True)
class Backend:
    kind: str
    detail: str


def _which(name: str) -> str | None:
    return shutil.which(name)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_S,
        check=False,
    )


def _run_checked(cmd: list[str]) -> subprocess.CompletedProcess:
    result = _run(cmd)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise WallpaperError(f"{cmd[0]} failed: {detail or 'exit ' + str(result.returncode)}")
    return result


def normalize_mode(mode: Any) -> str:
    value = str(mode or _DEFAULT_MODE).strip().lower()
    return value if value in SUPPORTED_MODES else _DEFAULT_MODE


def _state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "odysseus-helper"


def _state_path() -> Path:
    return _state_dir() / "wallpaper.json"


def load_state() -> dict:
    try:
        value = json.loads(_state_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    _state_dir().mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")


def _pid_alive(pid: Any) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _terminate(pid: Any) -> None:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return
    if not _pid_alive(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(20):
        if not _pid_alive(pid):
            return
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _swww_running() -> bool:
    if not _which("swww"):
        return False
    result = _run(["swww", "query"])
    return result.returncode == 0


def _dms_running() -> bool:
    """True when the Dux Modular Shell IPC answers with its outputs."""
    if not _which("dms"):
        return False
    result = _run(["dms", "ipc", "call", "outputs", "status"])
    return result.returncode == 0 and "outputs:" in result.stdout


def _dms_screens() -> list[str]:
    result = _run(["dms", "ipc", "call", "outputs", "status"])
    match = re.search(r"outputs:\s*(\[[^\]]*\])", result.stdout)
    if not match:
        return []
    try:
        value = json.loads(match.group(1))
    except ValueError:
        return []
    return [str(item) for item in value if isinstance(item, str)]


def detect_backend() -> Backend:
    desktop = (
        os.environ.get("XDG_CURRENT_DESKTOP")
        or os.environ.get("XDG_SESSION_DESKTOP")
        or ""
    ).casefold()

    if "gnome" in desktop and _which("gsettings"):
        return Backend("gnome", "GNOME")
    if ("kde" in desktop or "plasma" in desktop) and _which("plasma-apply-wallpaperimage"):
        return Backend("kde", "KDE Plasma")
    if "xfce" in desktop and _which("xfconf-query"):
        return Backend("xfce", "XFCE")
    if _dms_running():
        return Backend("dms", "Dux Modular Shell")
    if _swww_running():
        return Backend("swww", "swww")
    if _which("swaybg"):
        return Backend("swaybg", "swaybg")
    if desktop:
        return Backend(
            "unsupported",
            f"{desktop} has no supported wallpaper backend; start Dux Modular Shell or install swaybg/swww",
        )
    return Backend("unsupported", "no supported wallpaper backend detected")


def _valid_color(value: str) -> bool:
    text = str(value or "").strip()
    if not text.startswith("#"):
        return False
    body = text[1:]
    return len(body) in (3, 6) and all(ch in "0123456789abcdefABCDEF" for ch in body)


def _solid_image(color: str) -> str:
    """Render a solid-color PNG so DEs that only accept images can use a color."""
    tool = _which("magick") or _which("convert")
    if not tool:
        raise WallpaperError("color needs ImageMagick (magick or convert)")
    out = _state_dir() / f"solid-{color.lstrip('#').lower()}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    _run_checked([tool, "-size", "1920x1080", f"xc:{color}", str(out)])
    return str(out)


def _resolve_target(image: Any, color: Any) -> tuple[str | None, str | None]:
    image_value = str(image or "").strip()
    color_value = str(color or "").strip()
    if image_value and color_value:
        raise WallpaperError("provide image or color, not both")
    if image_value:
        path = Path(image_value).expanduser()
        if not path.is_file():
            raise WallpaperError(f"image not found: {image_value}")
        return str(path.resolve()), None
    if color_value:
        if not _valid_color(color_value):
            raise WallpaperError(f"invalid color: {color_value!r} (use #rrggbb)")
        return None, color_value
    raise WallpaperError("provide an image path or a color")


def _dry(commands: list[list[str]], backend: str) -> dict:
    return {
        "ok": True,
        "dry_run": True,
        "backend": backend,
        "changed": False,
        "commands": [" ".join(part for part in cmd) for cmd in commands],
        "message": "Dry run only; nothing was changed.",
    }


# ── GNOME ──────────────────────────────────────────────────────────────────


def _gsettings_get(schema: str, key: str) -> str:
    result = _run(["gsettings", "get", schema, key])
    return result.stdout.strip()


def _set_gnome(image: str, mode: str, dry_run: bool) -> dict:
    schema = "org.gnome.desktop.background"
    uri = Path(image).as_uri()
    options = _GNOME_MODE[mode]
    previous = {
        "picture_uri": _gsettings_get(schema, "picture-uri"),
        "picture_uri_dark": _gsettings_get(schema, "picture-uri-dark"),
        "picture_options": _gsettings_get(schema, "picture-options"),
    }
    commands = [
        ["gsettings", "set", schema, "picture-uri", uri],
        ["gsettings", "set", schema, "picture-uri-dark", uri],
        ["gsettings", "set", schema, "picture-options", options],
    ]
    if dry_run:
        return _dry(commands, "gnome")
    for cmd in commands:
        _run_checked(cmd)
    save_state({"backend": "gnome", "previous": previous})
    return {
        "ok": True,
        "backend": "gnome",
        "changed": True,
        "image": image,
        "mode": mode,
        "message": "Wallpaper changed on GNOME.",
    }


def _undo_gnome(previous: dict, dry_run: bool) -> dict:
    schema = "org.gnome.desktop.background"
    commands = []
    for key, state_key in (
        ("picture-uri", "picture_uri"),
        ("picture-uri-dark", "picture_uri_dark"),
        ("picture-options", "picture_options"),
    ):
        value = previous.get(state_key)
        if value:
            commands.append(["gsettings", "set", schema, key, value])
    if dry_run:
        return _dry(commands, "gnome")
    for cmd in commands:
        _run_checked(cmd)
    save_state({})
    return {"ok": True, "backend": "gnome", "changed": True, "message": "Previous wallpaper restored."}


# ── KDE ────────────────────────────────────────────────────────────────────


def _kde_current_image() -> str | None:
    """Best-effort read of the current Plasma wallpaper path."""
    config = Path.home() / ".config" / "plasma-org.kde.plasma.desktop-appletsrc"
    try:
        for line in config.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("Image=file://"):
                return line.split("=", 1)[1][len("file://"):]
    except OSError:
        return None
    return None


def _set_kde(image: str, mode: str, dry_run: bool) -> dict:
    command = ["plasma-apply-wallpaperimage", image]
    if dry_run:
        return _dry([command], "kde")
    previous = _kde_current_image()
    _run_checked(command)
    save_state({"backend": "kde", "previous": {"image": previous}})
    return {
        "ok": True,
        "backend": "kde",
        "changed": True,
        "image": image,
        "mode": mode,
        "message": "Wallpaper changed on KDE Plasma.",
    }


def _undo_kde(previous: dict, dry_run: bool) -> dict:
    image = previous.get("image")
    if not image:
        raise WallpaperError("the previous KDE wallpaper was not recorded")
    command = ["plasma-apply-wallpaperimage", image]
    if dry_run:
        return _dry([command], "kde")
    _run_checked(command)
    save_state({})
    return {"ok": True, "backend": "kde", "changed": True, "message": "Previous wallpaper restored."}


# ── XFCE ───────────────────────────────────────────────────────────────────


def _xfce_last_image_property() -> str:
    result = _run(["xfconf-query", "-c", "xfce4-desktop", "-l"])
    for line in result.stdout.splitlines():
        if line.strip().endswith("/last-image"):
            return line.strip()
    raise WallpaperError("could not find an XFCE last-image property")


def _set_xfce(image: str, mode: str, dry_run: bool) -> dict:
    prop = _xfce_last_image_property()
    current = _run(["xfconf-query", "-c", "xfce4-desktop", "-p", prop]).stdout.strip()
    command = ["xfconf-query", "-c", "xfce4-desktop", "-p", prop, "-s", image]
    if dry_run:
        return _dry([command], "xfce")
    _run_checked(command)
    save_state({"backend": "xfce", "previous": {"property": prop, "image": current}})
    return {
        "ok": True,
        "backend": "xfce",
        "changed": True,
        "image": image,
        "mode": mode,
        "message": "Wallpaper changed on XFCE.",
    }


def _undo_xfce(previous: dict, dry_run: bool) -> dict:
    prop = previous.get("property")
    image = previous.get("image")
    if not prop or not image:
        raise WallpaperError("the previous XFCE wallpaper was not recorded")
    command = ["xfconf-query", "-c", "xfce4-desktop", "-p", prop, "-s", image]
    if dry_run:
        return _dry([command], "xfce")
    _run_checked(command)
    save_state({})
    return {"ok": True, "backend": "xfce", "changed": True, "message": "Previous wallpaper restored."}


# ── swww ───────────────────────────────────────────────────────────────────


def _set_swww(image: str, mode: str, dry_run: bool) -> dict:
    command = ["swww", "img", image, "--transition-type", "none"]
    if dry_run:
        return _dry([command], "swww")
    previous = load_state().get("previous", {}).get("image")
    _run_checked(command)
    save_state({"backend": "swww", "previous": {"image": previous}})
    return {
        "ok": True,
        "backend": "swww",
        "changed": True,
        "image": image,
        "mode": mode,
        "message": "Wallpaper changed through swww.",
    }


def _undo_swww(previous: dict, dry_run: bool) -> dict:
    image = previous.get("image")
    if not image:
        raise WallpaperError("the previous swww wallpaper was not recorded")
    command = ["swww", "img", image, "--transition-type", "none"]
    if dry_run:
        return _dry([command], "swww")
    _run_checked(command)
    save_state({})
    return {"ok": True, "backend": "swww", "changed": True, "message": "Previous wallpaper restored."}


# ── swaybg ─────────────────────────────────────────────────────────────────


def _set_swaybg(image: str | None, color: str | None, mode: str, dry_run: bool) -> dict:
    exe = _which("swaybg")
    if not exe:
        raise WallpaperError("swaybg is not installed")
    state = load_state()
    previous_pid = state.get("pid") if state.get("backend") == "swaybg" else None

    command = [exe]
    if image:
        command += ["-i", image]
    else:
        command += ["-c", color]
    command += ["-m", mode]

    if dry_run:
        return _dry([command], "swaybg")

    if previous_pid and _pid_alive(previous_pid):
        _terminate(previous_pid)

    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    save_state(
        {
            "backend": "swaybg",
            "pid": process.pid,
            "started_by_helper": True,
            "previous": state.get("previous", {}),
            "image": image,
            "color": color,
            "mode": mode,
        }
    )
    return {
        "ok": True,
        "backend": "swaybg",
        "changed": True,
        "image": image,
        "color": color,
        "mode": mode,
        "message": (
            "Wallpaper set with swaybg. If another wallpaper manager is running, "
            "swaybg is layered on top; undo stops it and the previous wallpaper returns."
        ),
    }


def _undo_swaybg(state: dict, dry_run: bool) -> dict:
    pid = state.get("pid")
    if dry_run:
        return _dry([["kill", str(pid or "")]], "swaybg")
    _terminate(pid)
    save_state({})
    return {"ok": True, "backend": "swaybg", "changed": True, "message": "Helper wallpaper stopped; previous wallpaper is visible again."}


# ── Dux Modular Shell (DMS) ─────────────────────────────────────────────────


def _dms_checked(args: list[str]) -> str:
    """Run a ``dms ipc call`` and raise on its error text.

    DMS prints ``ERROR: ...`` to stdout and still exits 0, so return code alone
    is not enough to detect a failed call.
    """
    result = _run(["dms", "ipc", "call", *args])
    text = (result.stdout or "").strip()
    if result.returncode != 0 or text.startswith("ERROR:") or text.startswith("Function not found"):
        detail = text or (result.stderr or "").strip() or "dms command failed"
        raise WallpaperError(detail)
    return text


def _dms_wallpaper_state() -> tuple[bool, dict[str, str]]:
    """Return ``(global_mode, {screen_or_star: path})`` for DMS.

    DMS can be in global mode (one wallpaper for every output) or per-monitor
    mode. In per-monitor mode ``wallpaper get`` refuses and ``getFor`` is the
    only way to read a screen's wallpaper.
    """
    result = _run(["dms", "ipc", "call", "wallpaper", "get"])
    text = (result.stdout or "").strip()
    if result.returncode == 0 and not text.startswith("ERROR:"):
        return True, ({"*": text} if text else {})
    detail = (text + " " + (result.stderr or "")).casefold()
    if "per-monitor" not in detail:
        raise WallpaperError(f"dms wallpaper get failed: {text or 'unknown error'}")
    current: dict[str, str] = {}
    for screen in _dms_screens():
        path = _dms_checked(["wallpaper", "getFor", screen])
        if path:
            current[screen] = path
    return False, current


def _set_dms(image: str | None, color: str | None, mode: str, dry_run: bool) -> dict:
    target = image or _solid_image(color)
    global_mode, previous = _dms_wallpaper_state()
    if global_mode:
        commands = [["dms", "ipc", "call", "wallpaper", "set", target]]
    else:
        screens = _dms_screens()
        if not screens:
            raise WallpaperError("dms reports no connected outputs")
        commands = [
            ["dms", "ipc", "call", "wallpaper", "setFor", screen, target]
            for screen in screens
        ]
    if dry_run:
        return _dry(commands, "dms")
    for command in commands:
        _dms_checked(command[3:])
    save_state({"backend": "dms", "global": global_mode, "previous": previous})
    return {
        "ok": True,
        "backend": "dms",
        "changed": True,
        "image": target,
        "mode": mode,
        "message": "Wallpaper changed through Dux Modular Shell.",
    }


def _undo_dms(state: dict, dry_run: bool) -> dict:
    previous = state.get("previous") or {}
    if state.get("global"):
        image = previous.get("*")
        if not image:
            raise WallpaperError("the previous DMS wallpaper was not recorded")
        commands = [["dms", "ipc", "call", "wallpaper", "set", image]]
    else:
        commands = [
            ["dms", "ipc", "call", "wallpaper", "setFor", screen, path]
            for screen, path in previous.items()
            if path
        ]
        if not commands:
            commands = [["dms", "ipc", "call", "wallpaper", "clear"]]
    if dry_run:
        return _dry(commands, "dms")
    for command in commands:
        _dms_checked(command[3:])
    save_state({})
    return {
        "ok": True,
        "backend": "dms",
        "changed": True,
        "message": "Previous wallpaper restored.",
    }


# ── Public API ─────────────────────────────────────────────────────────────


def set_wallpaper(
    *,
    image: Any = None,
    color: Any = None,
    mode: Any = _DEFAULT_MODE,
    dry_run: bool = False,
    undo: bool = False,
) -> dict:
    """Change or restore the desktop wallpaper.

    Returns a result dict with ``ok``, ``backend``, ``changed``, and a
    human-readable ``message``. Raises :class:`WallpaperError` when the request
    cannot be honored.
    """
    mode = normalize_mode(mode)
    state = load_state()

    if undo:
        if not state:
            raise WallpaperError("no previous wallpaper was recorded by the helper")
        previous = state.get("previous", {})
        backend = state.get("backend")
        if backend == "gnome":
            return _undo_gnome(previous, dry_run)
        if backend == "kde":
            return _undo_kde(previous, dry_run)
        if backend == "xfce":
            return _undo_xfce(previous, dry_run)
        if backend == "dms":
            return _undo_dms(state, dry_run)
        if backend == "swww":
            return _undo_swww(previous, dry_run)
        if backend == "swaybg":
            return _undo_swaybg(state, dry_run)
        raise WallpaperError(f"cannot undo backend {backend!r}")

    image, color = _resolve_target(image, color)
    backend = detect_backend()

    if backend.kind == "gnome":
        return _set_gnome(image or _solid_image(color), mode, dry_run)
    if backend.kind == "kde":
        return _set_kde(image or _solid_image(color), mode, dry_run)
    if backend.kind == "xfce":
        return _set_xfce(image or _solid_image(color), mode, dry_run)
    if backend.kind == "dms":
        return _set_dms(image, color, mode, dry_run)
    if backend.kind == "swww":
        if not image:
            raise WallpaperError("swww needs an image; color is not supported")
        return _set_swww(image, mode, dry_run)
    if backend.kind == "swaybg":
        return _set_swaybg(image, color, mode, dry_run)

    raise WallpaperError(backend.detail)


def describe() -> dict:
    """Report the detected desktop, backend, and current helper state."""
    return {
        "desktop": os.environ.get("XDG_CURRENT_DESKTOP")
        or os.environ.get("XDG_SESSION_DESKTOP")
        or "",
        "wayland_display": os.environ.get("WAYLAND_DISPLAY", ""),
        "backend": detect_backend().kind,
        "backend_detail": detect_backend().detail,
        "state": load_state(),
    }


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Change the desktop wallpaper.")
    parser.add_argument("--image")
    parser.add_argument("--color")
    parser.add_argument("--mode", default=_DEFAULT_MODE, choices=SUPPORTED_MODES)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--undo", action="store_true")
    parser.add_argument("--describe", action="store_true")
    args = parser.parse_args()

    try:
        if args.describe:
            result = describe()
        else:
            result = set_wallpaper(
                image=args.image,
                color=args.color,
                mode=args.mode,
                dry_run=args.dry_run,
                undo=args.undo,
            )
    except WallpaperError as error:
        print(json.dumps({"ok": False, "error": str(error)}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
