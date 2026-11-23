# Desktop Helper

A small, local capability layer for Odysseus. It exposes a few **named desktop
verbs** over MCP stdio so the engine can propose them without giving the agent a
shell.

This is the first vertical slice of the desktop-helper design in
[`docs/desktop-helper-design.md`](../docs/desktop-helper-design.md). The tray
app and simple-mode UI are not built yet; this directory is the capability
layer they will drive.

## Verbs

| Tool | Effects | Reversible | What it does |
| --- | --- | --- | --- |
| `set_wallpaper` | `ui_side_effect` | yes (`undo=true`) | Changes the desktop wallpaper, or restores the previous one |
| `check_updates` | `read_private` | n/a | Reads available package updates from the local cache |

There is deliberately **no generic command runner**. Each verb is fixed and
typed.

## Requirements

- The Odysseus virtualenv (the `mcp` package is already a dependency).
- A supported wallpaper backend:
  - Dux Modular Shell (DMS): preferred when running; uses `dms ipc call wallpaper`
  - GNOME: `gsettings`
  - KDE Plasma: `plasma-apply-wallpaperimage`
  - XFCE: `xfconf-query`
  - Wayland (Sway, Niri, Hyprland) without DMS: `swaybg` or a running `swww`
- ImageMagick (`magick`/`convert`) only if you ask for a solid color on a
  desktop backend that requires an image file.

DMS is preferred when it is running because it owns the background layer.
Layering `swaybg` on top would cover the shell's wallpaper and leave DMS's
settings out of sync. The helper uses DMS's own IPC, including per-monitor
mode. If DMS is not running, it falls back to `swaybg` or `swww`.

## Run it manually

```bash
cd ~/odysseus
./venv/bin/python desktop_helper/wallpaper.py --describe
./venv/bin/python desktop_helper/wallpaper.py --image /path/to/dark.png --dry-run
./venv/bin/python desktop_helper/wallpaper.py --image /path/to/dark.png
./venv/bin/python desktop_helper/wallpaper.py --undo
./venv/bin/python desktop_helper/updates.py
```

The MCP server itself is `desktop_helper/helper_server.py`; it speaks stdio and
is not meant to be run by hand except for debugging.

## Preview UI (no MCP client needed)

`desktop_helper/preview_server.py` serves a small local page so you can try the
verbs without Odysseus or an MCP client:

```bash
./venv/bin/python desktop_helper/preview_server.py
```

Open <http://127.0.0.1:8791/>. The page shows the detected desktop and backend,
lets you set a wallpaper (image or solid color, with a dry-run preview), undo
the last change, and check for updates.

This is a **preview of the verbs, not the MCP transport**. It imports the same
functions the MCP server calls, so it tests behavior but not the protocol. It
binds to `127.0.0.1` only and has no authentication; do not expose it to a
network. Change the port with `ODYSSEUS_HELPER_PREVIEW_PORT`.

## Register with Odysseus

Use the **admin** MCP settings (Settings → Integrations) or
`POST /api/mcp/servers`. Do not use the agent-facing `manage_mcp` tool: it
deliberately blocks interpreters and cannot register this server.

```bash
curl -X POST http://127.0.0.1:7011/api/mcp/servers \
  -H "Cookie: <your session cookie>" \
  -F "name=Desktop Helper" \
  -F "transport=stdio" \
  -F "command=/home/erick/odysseus/venv/bin/python" \
  -F "args=[\"/home/erick/odysseus/desktop_helper/helper_server.py\"]" \
  -F "env={}"
```

Then declare effects and trigger phrases:

```bash
curl -X PATCH http://127.0.0.1:7011/api/mcp/servers/<server_id>/tool-effects \
  -H "Content-Type: application/json" \
  -H "Cookie: <your session cookie>" \
  -d '{
    "tools": {
      "set_wallpaper": {
        "effects": ["ui_side_effect"],
        "triggers": ["change my wallpaper", "set my wallpaper", "new wallpaper", "dark wallpaper"]
      },
      "check_updates": {
        "effects": ["read_private"],
        "triggers": ["check for updates", "any updates", "is my system up to date"]
      }
    }
  }'
```

The trigger phrases let a user say "change my wallpaper to something dark"
without naming `mcp__<server>__set_wallpaper`. Triggers only admit the tool;
they never widen its effects. Destructive effects cannot carry triggers, and
ambiguous trigger matches fail closed.

## Tool arguments

### `set_wallpaper`

| Argument | Type | Notes |
| --- | --- | --- |
| `image` | string | Absolute path to an image |
| `color` | string | `#rrggbb`, used when no image is given |
| `mode` | string | `fill` (default), `fit`, `stretch`, `center`, `tile` |
| `dry_run` | boolean | Return the command without changing anything |
| `undo` | boolean | Restore the wallpaper recorded before the last change |

Provide exactly one of `image` or `color` (or set `undo`).

### `check_updates`

| Argument | Type | Notes |
| --- | --- | --- |
| `refresh` | boolean | Not supported; the tool explains why instead of escalating |

The check reads the local package cache (`pacman -Qu`, `apt list --upgradable`,
`dnf check-update`, `zypper list-updates`). It never installs and never uses
sudo.

## State

The helper records the previous wallpaper in
`${XDG_STATE_HOME:-~/.local/state}/odysseus-helper/wallpaper.json` so `undo`
works across calls. For `swaybg`, the helper owns the process and stops it on
undo; it never kills a wallpaper daemon it did not start.

## Safety

- No shell, no package installation, no privileged commands.
- `check_updates` is read-only.
- `set_wallpaper` is reversible and reports exactly what it changed.
- Unsupported desktops return a clear error rather than guessing.
- The engine's access level still decides whether a card appears; this layer
  only performs the action once the engine admits it.

## Tests

```bash
./venv/bin/python -m pytest -q tests/test_desktop_helper_wallpaper.py
```
