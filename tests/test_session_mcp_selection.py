"""Per-chat MCP opt-out parsing (client payload -> session list)."""

from routes.chat_routes import _parse_mcp_disabled_payload
from routes.session_routes import _parse_mcp_disabled_servers


def test_absent_payload_means_no_override():
    assert _parse_mcp_disabled_payload(None) is None
    assert _parse_mcp_disabled_payload("") is None


def test_payload_is_normalized_and_deduplicated():
    assert _parse_mcp_disabled_payload('["b", "a", "a"]') == ["a", "b"]


def test_payload_drops_malformed_entries():
    assert _parse_mcp_disabled_payload(
        '["ok-id", "", "bad id", "semi;colon", null, 42]'
    ) == ["42", "ok-id"]


def test_non_list_payload_is_ignored():
    assert _parse_mcp_disabled_payload('{"a": 1}') is None
    assert _parse_mcp_disabled_payload("not json") is None


def test_stored_column_parsing():
    assert _parse_mcp_disabled_servers(None) == []
    assert _parse_mcp_disabled_servers("") == []
    assert _parse_mcp_disabled_servers('["x", "y", "x"]') == ["x", "y"]
    assert _parse_mcp_disabled_servers("broken") == []
    assert _parse_mcp_disabled_servers('"not-a-list"') == []
