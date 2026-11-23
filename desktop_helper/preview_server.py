"""Local preview UI for the desktop helper verbs.

Serves a small page on http://127.0.0.1:8791 so you can try ``set_wallpaper``
and ``check_updates`` without going through Odysseus or an MCP client.

This is a *preview* of the verbs, not the MCP transport. The MCP server that
Odysseus calls is ``desktop_helper/helper_server.py``. This page imports the
same functions directly, so it exercises the behavior but not the protocol.

Local-only: it binds to 127.0.0.1 and has no authentication. Do not expose it
to a network.

Run::

    /path/to/venv/bin/python desktop_helper/preview_server.py
"""

from __future__ import annotations

import json
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from desktop_helper import updates, wallpaper  # noqa: E402

DEFAULT_PORT = 8791
MAX_BODY_BYTES = 64 * 1024


def call_tool(tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run one helper verb and return a JSON-ready result.

    Kept separate from the HTTP handler so it can be tested without a server.
    """
    arguments = arguments or {}
    try:
        if tool == "set_wallpaper":
            return wallpaper.set_wallpaper(
                image=arguments.get("image"),
                color=arguments.get("color"),
                mode=arguments.get("mode", "fill"),
                dry_run=bool(arguments.get("dry_run", False)),
                undo=bool(arguments.get("undo", False)),
            )
        if tool == "check_updates":
            return updates.check_updates(refresh=bool(arguments.get("refresh", False)))
        if tool == "describe":
            return {"ok": True, **wallpaper.describe()}
        return {"ok": False, "error": f"unknown tool: {tool}"}
    except wallpaper.WallpaperError as error:
        return {"ok": False, "error": str(error)}
    except Exception as error:  # keep the preview alive; report the failure
        return {"ok": False, "error": f"{type(error).__name__}: {error}"}


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Desktop Helper Preview</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 2rem 1.25rem;
    font: 15px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
    background: #101014; color: #e6e6ea;
  }
  main { max-width: 780px; margin: 0 auto; }
  h1 { font-size: 1.35rem; margin: 0 0 .25rem; font-weight: 600; }
  .sub { color: #9a9aa6; margin: 0 0 1.5rem; font-size: .9rem; }
  .card {
    background: #17171d; border: 1px solid #26262f; border-radius: 12px;
    padding: 1.1rem 1.15rem; margin-bottom: 1rem;
  }
  .card h2 { font-size: .95rem; margin: 0 0 .75rem; font-weight: 600; color: #cfcfe0; }
  label { display: block; font-size: .82rem; color: #9a9aa6; margin: .55rem 0 .2rem; }
  input, select {
    width: 100%; padding: .5rem .6rem; border-radius: 8px;
    border: 1px solid #33333f; background: #101016; color: #e6e6ea;
    font: inherit;
  }
  .row { display: flex; gap: .75rem; flex-wrap: wrap; }
  .row > div { flex: 1 1 180px; }
  .actions { display: flex; gap: .5rem; flex-wrap: wrap; margin-top: .9rem; }
  button {
    font: inherit; font-weight: 500; padding: .5rem .9rem; border-radius: 8px;
    border: 1px solid #3a3a4a; background: #23232d; color: #e6e6ea;
    cursor: pointer;
  }
  button:hover { background: #2c2c38; }
  button.primary { background: #3b5bdb; border-color: #3b5bdb; color: #fff; }
  button.primary:hover { background: #4c6ef5; }
  button.danger { background: #2a1a1e; border-color: #5a2a33; color: #ffb3c0; }
  .check { display: flex; align-items: center; gap: .45rem; margin-top: .75rem; font-size: .85rem; color: #b9b9c7; }
  .check input { width: auto; }
  .status { font-size: .82rem; color: #9a9aa6; margin-top: .5rem; }
  pre {
    margin: .9rem 0 0; padding: .9rem; border-radius: 10px;
    background: #0b0b0f; border: 1px solid #26262f;
    font: 12.5px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace;
    color: #c9c9d6; white-space: pre-wrap; word-break: break-word;
    max-height: 340px; overflow: auto;
  }
  .hint { color: #7c7c8a; font-size: .8rem; margin-top: .6rem; }
  .warn { color: #ffcf8a; }
</style>
</head>
<body>
<main>
  <h1>Desktop Helper Preview</h1>
  <p class="sub">Local test page for the helper verbs. It calls the same functions the MCP server uses, without the MCP transport.</p>

  <div class="card">
    <h2>Environment</h2>
    <div class="actions">
      <button id="describe">Show desktop + backend</button>
    </div>
    <div class="status" id="env"></div>
  </div>

  <div class="card">
    <h2>Set wallpaper</h2>
    <div class="row">
      <div>
        <label for="image">Image path (absolute)</label>
        <input id="image" placeholder="/home/you/Pictures/dark.png" autocomplete="off">
      </div>
      <div>
        <label for="color">or solid color</label>
        <input id="color" placeholder="#101014" autocomplete="off">
      </div>
      <div>
        <label for="mode">Fit mode</label>
        <select id="mode">
          <option value="fill">fill</option>
          <option value="fit">fit</option>
          <option value="stretch">stretch</option>
          <option value="center">center</option>
          <option value="tile">tile</option>
        </select>
      </div>
    </div>
    <label class="check"><input type="checkbox" id="dryRun"> Preview only (dry run)</label>
    <div class="actions">
      <button class="primary" id="apply">Apply wallpaper</button>
      <button class="danger" id="undo">Undo last change</button>
    </div>
    <p class="hint">Provide an image or a color. Undo restores the wallpaper recorded before the last change.</p>
  </div>

  <div class="card">
    <h2>Check for updates</h2>
    <div class="actions">
      <button id="updates">Check updates</button>
    </div>
    <p class="hint">Read-only. It reads the local package cache and never installs or uses sudo.</p>
  </div>

  <div class="card">
    <h2>Result</h2>
    <pre id="out">No calls yet.</pre>
  </div>
</main>

<script>
const out = document.getElementById("out");
const env = document.getElementById("env");

function show(value) {
  out.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

async function call(tool, args = {}) {
  show("Calling " + tool + "…");
  try {
    const res = await fetch("/api/call", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool, arguments: args }),
    });
    const data = await res.json();
    show(data);
  } catch (error) {
    show({ ok: false, error: String(error) });
  }
}

document.getElementById("describe").addEventListener("click", async () => {
  try {
    const res = await fetch("/api/describe");
    const data = await res.json();
    env.textContent = "desktop: " + (data.desktop || "unknown") +
      " · backend: " + (data.backend || "unknown") +
      (data.backend_detail ? " (" + data.backend_detail + ")" : "");
    show(data);
  } catch (error) {
    show({ ok: false, error: String(error) });
  }
});

document.getElementById("apply").addEventListener("click", () => {
  const image = document.getElementById("image").value.trim();
  const color = document.getElementById("color").value.trim();
  const mode = document.getElementById("mode").value;
  const dry_run = document.getElementById("dryRun").checked;
  if (!image && !color) {
    show({ ok: false, error: "Provide an image path or a color." });
    return;
  }
  call("set_wallpaper", { image: image || null, color: color || null, mode, dry_run });
});

document.getElementById("undo").addEventListener("click", () => {
  call("set_wallpaper", { undo: true });
});

document.getElementById("updates").addEventListener("click", () => {
  call("check_updates");
});
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "OdysseusHelperPreview/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter default logging
        sys.stderr.write("preview: " + (fmt % args) + "\n")

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        self._send(
            status,
            json.dumps(payload, indent=2).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._send(HTTPStatus.OK, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if self.path == "/api/describe":
            self._json(HTTPStatus.OK, call_tool("describe"))
            return
        if self.path == "/api/health":
            self._json(HTTPStatus.OK, {"ok": True})
            return
        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/call":
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "body too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as error:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid JSON: {error}"})
            return
        if not isinstance(payload, dict):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "expected a JSON object"})
            return
        tool = str(payload.get("tool") or "")
        arguments = payload.get("arguments") or {}
        if not isinstance(arguments, dict):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "arguments must be an object"})
            return
        self._json(HTTPStatus.OK, call_tool(tool, arguments))


def main() -> int:
    port = int(os.environ.get("ODYSSEUS_HELPER_PREVIEW_PORT", DEFAULT_PORT))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Desktop helper preview: http://127.0.0.1:{port}/", flush=True)
    print("Local only. Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
