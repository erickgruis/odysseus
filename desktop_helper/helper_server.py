"""Odysseus desktop helper MCP server.

Exposes the helper's narrow desktop verbs over stdio so the Odysseus engine can
call them as local MCP tools. The engine proposes; this process performs.

Tools:
- ``set_wallpaper``
- ``check_updates``

Run directly::

    /path/to/venv/bin/python /path/to/desktop_helper/helper_server.py

Register it in Odysseus through the admin MCP settings (Settings > Integrations)
or ``POST /api/mcp/servers``. The agent-facing ``manage_mcp`` path deliberately
blocks interpreters, so it cannot register this server.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from desktop_helper import updates, wallpaper  # noqa: E402

server = Server("odysseus-desktop-helper")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="set_wallpaper",
            description=(
                "Change the desktop wallpaper. Use for requests like 'change my "
                "wallpaper', 'set a dark background', or 'use this image as my "
                "wallpaper'. Reversible: pass undo=true to restore the previous "
                "wallpaper. Pass dry_run=true to preview without changing anything."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "Absolute path to an image file.",
                    },
                    "color": {
                        "type": "string",
                        "description": "Hex color like #101014, used when no image is given.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": list(wallpaper.SUPPORTED_MODES),
                        "description": "How to fit the image. Default fill.",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "Preview the command without changing anything.",
                    },
                    "undo": {
                        "type": "boolean",
                        "description": "Restore the wallpaper recorded before the last change.",
                    },
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="check_updates",
            description=(
                "Check for available system package updates without installing "
                "anything. Returns the package manager, count, and a sample. This "
                "reads the local cache; it does not refresh it."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "refresh": {
                        "type": "boolean",
                        "description": "Ask to refresh first. Not supported; the tool explains why.",
                    },
                },
                "additionalProperties": False,
            },
        ),
    ]


def _text(payload: dict) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, indent=2))]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    arguments = arguments or {}
    try:
        if name == "set_wallpaper":
            return _text(
                wallpaper.set_wallpaper(
                    image=arguments.get("image"),
                    color=arguments.get("color"),
                    mode=arguments.get("mode", "fill"),
                    dry_run=bool(arguments.get("dry_run", False)),
                    undo=bool(arguments.get("undo", False)),
                )
            )
        if name == "check_updates":
            return _text(
                updates.check_updates(refresh=bool(arguments.get("refresh", False)))
            )
        return _text({"ok": False, "error": f"unknown tool: {name}"})
    except wallpaper.WallpaperError as error:
        return _text({"ok": False, "error": str(error)})
    except Exception as error:  # keep the server alive; report the failure
        return _text({"ok": False, "error": f"{type(error).__name__}: {error}"})


async def run() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(run())
