"""Declared per-tool effects for local MCP servers.

The database declaration is the operator-authored replacement for the
read/write env shorthands. A declared tool is classified by its effects, not
by a boolean, so a desktop helper can expose verbs such as ``set_wallpaper``
(``ui_side_effect``) or ``install_updates`` (``admin_change`` +
``destructive``) without being treated as a generic shell.

Security invariants preserved here:
1. A declared tool is still ``EXTERNAL_UNTRUSTED``: its output arms the gate.
2. Unknown, malformed, empty, or contradictory declarations fail closed.
3. A tool's own ``destructiveHint`` vetoes a declaration that omits
   ``destructive``.
4. The database declaration wins over the env shorthand for the same tool.
5. Builtin servers and built-in tool names cannot be declared.
"""

from types import SimpleNamespace

import pytest

from src.tool_capabilities import (
    LOCAL_MCP_READ_TOOLS_ENV,
    LOCAL_MCP_WRITE_TOOLS_ENV,
    ResultIntegrity,
    ToolEffect,
    capabilities_for_tool,
    declared_effects_for_tool,
    declared_local_tool_names,
    is_declared_local_mcp_tool,
    refresh_local_tool_declarations,
    set_local_tool_declarations,
)

SERVER = "a1b2c3d4"
TOOL = f"mcp__{SERVER}__set_wallpaper"
INSTALL = f"mcp__{SERVER}__install_updates"
READ = f"mcp__{SERVER}__search_notes"


@pytest.fixture(autouse=True)
def reset_declarations():
    set_local_tool_declarations({})
    yield
    set_local_tool_declarations({})


def _install_manager(tools):
    from src.tool_utils import set_mcp_manager

    manager = SimpleNamespace(
        _tools={SERVER: tools},
        _connections={
            SERVER: {
                "status": "connected",
                "name": SERVER,
                "transport": "stdio",
                "tool_count": len(tools),
                "identity": "",
            }
        },
        is_builtin=lambda sid: False,
        get_server_status=lambda sid: {
            "status": "connected",
            "transport": "stdio",
        },
        resource_identity=lambda name: SimpleNamespace(
            namespace="mcp", endpoint_id=f"stdio:{SERVER}"
        ),
    )
    set_mcp_manager(manager)
    return manager


def test_ui_effect_declaration_classified():
    set_local_tool_declarations({TOOL: ["ui_side_effect"]})

    capabilities = capabilities_for_tool(TOOL)
    assert capabilities.known is True
    assert capabilities.effects == frozenset({ToolEffect.UI_SIDE_EFFECT})
    assert capabilities.result_integrity is ResultIntegrity.EXTERNAL_UNTRUSTED


def test_multi_effect_declaration_classified():
    set_local_tool_declarations(
        {INSTALL: ["admin_change", "destructive"]}
    )

    capabilities = capabilities_for_tool(INSTALL)
    assert capabilities.known is True
    assert capabilities.effects == frozenset(
        {ToolEffect.ADMIN_CHANGE, ToolEffect.DESTRUCTIVE}
    )


def test_unknown_effect_fails_closed():
    set_local_tool_declarations({TOOL: ["not_a_real_effect"]})

    assert capabilities_for_tool(TOOL).known is False
    assert declared_effects_for_tool(TOOL) is None


def test_empty_effect_list_fails_closed():
    set_local_tool_declarations({TOOL: []})

    assert capabilities_for_tool(TOOL).known is False


def test_destructive_hint_vetoes_declaration_without_destructive():
    _install_manager(
        [{"name": "set_wallpaper", "annotations": {"destructiveHint": True}}]
    )
    set_local_tool_declarations({TOOL: ["ui_side_effect"]})

    assert capabilities_for_tool(TOOL).known is False


def test_destructive_hint_allows_destructive_declaration():
    _install_manager(
        [{"name": "set_wallpaper", "annotations": {"destructiveHint": True}}]
    )
    set_local_tool_declarations({TOOL: ["ui_side_effect", "destructive"]})

    capabilities = capabilities_for_tool(TOOL)
    assert capabilities.known is True
    assert ToolEffect.DESTRUCTIVE in capabilities.effects


def test_env_shorthand_still_works(monkeypatch):
    from src.tool_utils import set_mcp_manager

    set_mcp_manager(None)
    monkeypatch.setenv(LOCAL_MCP_READ_TOOLS_ENV, READ)
    monkeypatch.delenv(LOCAL_MCP_WRITE_TOOLS_ENV, raising=False)
    set_local_tool_declarations({})

    capabilities = capabilities_for_tool(READ)
    assert capabilities.known is True
    assert capabilities.effects == frozenset({ToolEffect.READ_PRIVATE})


def test_database_declaration_wins_over_env(monkeypatch):
    from src.tool_utils import set_mcp_manager

    set_mcp_manager(None)
    monkeypatch.setenv(LOCAL_MCP_READ_TOOLS_ENV, READ)
    monkeypatch.delenv(LOCAL_MCP_WRITE_TOOLS_ENV, raising=False)
    set_local_tool_declarations({READ: ["ui_side_effect"]})

    capabilities = capabilities_for_tool(READ)
    assert capabilities.effects == frozenset({ToolEffect.UI_SIDE_EFFECT})


def test_declared_names_include_database_tools():
    set_local_tool_declarations({TOOL: ["ui_side_effect"]})

    assert TOOL in declared_local_tool_names()
    assert is_declared_local_mcp_tool(TOOL) is True


def test_builtin_server_cannot_be_declared():
    builtin = "mcp__builtin_browser__browser_snapshot"
    set_local_tool_declarations({builtin: ["ui_side_effect"]})

    # The declaration is ignored; the registry classification still wins.
    assert is_declared_local_mcp_tool(builtin) is False
    capabilities = capabilities_for_tool(builtin)
    assert capabilities.known is True
    assert capabilities.effects == frozenset({ToolEffect.BROKERED_NETWORK_READ})


def test_authority_admits_explicitly_named_ui_tool():
    from src.agent_runtime.authority import (
        SemanticIntent,
        _trusted_local_mcp_request_tools,
    )

    _install_manager([{"name": "set_wallpaper"}])
    set_local_tool_declarations({TOOL: ["ui_side_effect"]})

    intent = SemanticIntent(frozenset(), None, None)
    admitted = _trusted_local_mcp_request_tools(f"run {TOOL} now", intent)

    assert TOOL in admitted


def test_auto_runs_ui_effect_without_approval():
    from src.tool_access import requires_approval

    needs, _ = requires_approval("auto", [ToolEffect.UI_SIDE_EFFECT])
    assert needs is False

    needs, _ = requires_approval(
        "auto", [ToolEffect.ADMIN_CHANGE, ToolEffect.DESTRUCTIVE]
    )
    assert needs is True


def test_ask_cards_ui_effect():
    from src.tool_access import requires_approval

    needs, _ = requires_approval("ask", [ToolEffect.UI_SIDE_EFFECT])
    assert needs is True


def test_database_loader_reads_tool_effects(monkeypatch):
    from core import database as core_database

    row = SimpleNamespace(
        id=SERVER,
        tool_effects='{"set_wallpaper": ["ui_side_effect"]}',
    )

    class _Query:
        def all(self):
            return [row]

    class _Session:
        def query(self, _model):
            return _Query()

        def close(self):
            pass

    monkeypatch.setattr(core_database, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(core_database, "McpServer", object)

    refresh_local_tool_declarations()

    capabilities = capabilities_for_tool(TOOL)
    assert capabilities.known is True
    assert capabilities.effects == frozenset({ToolEffect.UI_SIDE_EFFECT})
