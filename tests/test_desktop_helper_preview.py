"""Tests for the desktop helper's local preview server.

These do not touch a real desktop. They exercise the dispatch function and the
HTTP routes against a server bound to an ephemeral loopback port.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from desktop_helper import preview_server, wallpaper


def test_call_tool_unknown():
    result = preview_server.call_tool("nope")
    assert result["ok"] is False
    assert "unknown tool" in result["error"]


def test_call_tool_routes_wallpaper(monkeypatch):
    seen = {}

    def fake_set_wallpaper(**kwargs):
        seen.update(kwargs)
        return {"ok": True, "changed": True}

    monkeypatch.setattr(wallpaper, "set_wallpaper", fake_set_wallpaper)
    result = preview_server.call_tool(
        "set_wallpaper",
        {"image": "/tmp/a.png", "mode": "fit", "dry_run": True},
    )
    assert result == {"ok": True, "changed": True}
    assert seen["image"] == "/tmp/a.png"
    assert seen["mode"] == "fit"
    assert seen["dry_run"] is True


def test_call_tool_maps_wallpaper_error(monkeypatch):
    def boom(**kwargs):
        raise wallpaper.WallpaperError("no backend")

    monkeypatch.setattr(wallpaper, "set_wallpaper", boom)
    result = preview_server.call_tool("set_wallpaper", {"color": "#101014"})
    assert result["ok"] is False
    assert result["error"] == "no backend"


@pytest.fixture()
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), preview_server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_health_route(server):
    with urllib.request.urlopen(server + "/api/health", timeout=5) as response:
        assert response.status == 200
        assert json.loads(response.read())["ok"] is True


def test_index_serves_page(server):
    with urllib.request.urlopen(server + "/", timeout=5) as response:
        body = response.read().decode("utf-8")
        assert response.status == 200
        assert "Desktop Helper Preview" in body
        assert "set_wallpaper" in body


def test_call_route_round_trip(server):
    request = urllib.request.Request(
        server + "/api/call",
        data=json.dumps({"tool": "nope", "arguments": {}}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        result = json.loads(response.read())
        assert result["ok"] is False
        assert "unknown tool" in result["error"]


def test_call_route_rejects_bad_json(server):
    request = urllib.request.Request(
        server + "/api/call",
        data=b"{not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=5)
    assert excinfo.value.code == 400
