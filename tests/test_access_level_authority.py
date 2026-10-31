"""Access levels widen request authority; policy denials still win.

Regression guard for the "Allow once / Full Access" failures: request
authority used to be derived only from the latest message, so an explicit
user permission decision could not admit a tool the sentence did not mention.
"""

import pytest

from src.agent_runtime.authority import ExactOperation, create_request_authority
from src.tool_access import (
    ASK,
    AUTO,
    FULL_ACCESS,
    READ_ONLY,
    widen_authority_for_level,
)
from src.tool_policy import ToolPolicy

BASH = ExactOperation.normalize("bash", "pwd && ls")


@pytest.fixture
def authority(tmp_path):
    # A sentence with no shell intent: before the fix this granted only
    # ask_user/update_plan, so bash was refused no matter what the user chose.
    return create_request_authority(
        "look at the machine on the local network and tell me about it",
        owner="alice",
        session_id="session-1",
        workspace=str(tmp_path),
    )


def test_intent_only_message_does_not_grant_bash(authority):
    assert not authority.permits(BASH)
    assert not authority.restricted(BASH)


@pytest.mark.parametrize("level", [ASK, AUTO, FULL_ACCESS])
def test_interactive_levels_grant_bash(authority, level):
    widened = widen_authority_for_level(authority, level)

    assert widened.permits(BASH)
    # The executor needs a sealed launch scope for shell tools.
    assert widened.launch_scopes


def test_read_only_never_widens(authority):
    assert widen_authority_for_level(authority, READ_ONLY) is authority


def test_no_level_leaves_authority_untouched(authority):
    assert widen_authority_for_level(authority, None) is authority


def test_tool_policy_denial_survives_widening(authority):
    widened = widen_authority_for_level(authority, FULL_ACCESS)
    restricted = widened.restrict(ToolPolicy(disabled_tools=frozenset({"bash"})))

    assert restricted.restricted(BASH)
    assert not restricted.permits(BASH)


def test_widening_is_idempotent(authority):
    once = widen_authority_for_level(authority, ASK)
    twice = widen_authority_for_level(once, ASK)
    assert twice is once


@pytest.mark.asyncio
async def test_agent_loop_binds_the_widened_authority(tmp_path):
    """with_request_authority applies the level before the loop runs."""
    from src.agent_runtime.authority import (
        MISSING_AUTHORITY,
        active_request_authority,
        with_request_authority,
    )

    @with_request_authority
    async def probe(messages, access_level=None, owner=None, session_id=None,
                    workspace=None, request_authority=MISSING_AUTHORITY):
        yield active_request_authority()

    async for bound in probe(
        [{"role": "user", "content": "look at the machine on the local network"}],
        access_level=ASK,
        owner="alice",
        session_id="session-1",
        workspace=str(tmp_path),
    ):
        assert bound.permits(BASH)

    async for bound in probe(
        [{"role": "user", "content": "look at the machine on the local network"}],
        access_level=None,
        owner="alice",
        session_id="session-1",
        workspace=str(tmp_path),
    ):
        assert not bound.permits(BASH)


class _FakeMcpManager:
    def get_all_tools(self):
        return [
            {"server_id": "custom", "name": "jev_classify",
             "qualified_name": "mcp__custom__jev_classify"},
        ]


CUSTOM_MCP = ExactOperation.normalize("mcp__custom__jev_classify", "{}")


def test_full_access_grants_custom_mcp_tools(monkeypatch, tmp_path):
    import src.tool_utils as tool_utils

    monkeypatch.setattr(tool_utils, "_mcp_manager", _FakeMcpManager())
    authority = create_request_authority(
        "look at the machine on the local network",
        owner="alice", session_id="s", workspace=str(tmp_path),
    )
    assert not authority.permits(CUSTOM_MCP)

    widened = widen_authority_for_level(authority, FULL_ACCESS)
    assert widened.permits(CUSTOM_MCP)


def test_read_only_never_grants_custom_mcp(monkeypatch, tmp_path):
    import src.tool_utils as tool_utils

    monkeypatch.setattr(tool_utils, "_mcp_manager", _FakeMcpManager())
    authority = create_request_authority(
        "look at the machine on the local network",
        owner="alice", session_id="s", workspace=str(tmp_path),
    )
    assert not widen_authority_for_level(authority, READ_ONLY).permits(CUSTOM_MCP)


def test_per_chat_mcp_opt_out_wins_over_the_level(monkeypatch, tmp_path):
    import src.tool_utils as tool_utils

    monkeypatch.setattr(tool_utils, "_mcp_manager", _FakeMcpManager())
    authority = create_request_authority(
        "look at the machine on the local network",
        owner="alice", session_id="s", workspace=str(tmp_path),
    )
    widened = widen_authority_for_level(authority, FULL_ACCESS)
    restricted = widened.restrict(
        ToolPolicy(disabled_tools=frozenset({"mcp__custom__jev_classify"}))
    )
    assert restricted.restricted(CUSTOM_MCP)
    assert not restricted.permits(CUSTOM_MCP)
