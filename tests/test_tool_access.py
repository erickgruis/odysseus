"""Interactive access levels: taxonomy, enforcement, and persistence.

The level is a UI choice that must be enforced server-side, so these tests
exercise the enforcement seams directly rather than the browser:

* ``src.tool_access`` maps a level to "ask or not" for a given effect set.
* ``ToolRunSecurityContext.decision_for`` applies the level before the legacy
  external-context gate, and denial kinds that no approval can lift report
  ``approvable=False`` so the loop does not offer a dead card.
* Session rows store the level; ``core.session_manager`` round-trips it.
"""

import pytest

from core.database import Session as DbSession
from core.models import Session
from core.session_manager import SessionManager
from src.tool_access import (
    ASK,
    AUTO,
    DEFAULT_LEVEL,
    FULL_ACCESS,
    ORDER,
    READ_ONLY,
    clamp_access_level,
    disable_mcp_for,
    disabled_tools_for,
    normalize_access_level,
    requires_approval,
)
from src.tool_capabilities import ToolRunSecurityContext, capabilities_for_action


def _effects(tool_name):
    return capabilities_for_action(tool_name, "{}").effects


# ── Taxonomy ──────────────────────────────────────────────────────────────


def test_levels_are_ordered_from_safest_to_most_permissive():
    assert ORDER == (READ_ONLY, ASK, AUTO, FULL_ACCESS)
    assert DEFAULT_LEVEL == ASK
    assert normalize_access_level("full") == FULL_ACCESS
    assert normalize_access_level("workspace_write") == AUTO
    assert normalize_access_level("read-only") == READ_ONLY
    assert normalize_access_level("nonsense", default=None) is None


def test_non_admin_and_delegated_callers_cannot_select_full_access():
    assert (
        clamp_access_level(FULL_ACCESS, admin=False, delegated=False) == AUTO
    )
    assert (
        clamp_access_level(FULL_ACCESS, admin=True, delegated=True) == ASK
    )
    # Lower levels pass through for everyone.
    assert clamp_access_level(READ_ONLY, admin=False, delegated=True) == READ_ONLY


def test_delegated_runs_keep_the_legacy_gate_instead_of_an_unanswerable_card():
    # A token cannot answer an approval card (chat_routes refuses it), so an
    # asking level would stall every mutating call. Those runs fall back to the
    # legacy gate; an explicit Read Only choice is a restriction and applies.
    from src.tool_access import enforced_level

    assert enforced_level(ASK, delegated=True) is None
    assert enforced_level(AUTO, delegated=True) is None
    assert enforced_level(FULL_ACCESS, delegated=True) is None
    assert enforced_level(READ_ONLY, delegated=True) == READ_ONLY
    assert enforced_level(ASK, delegated=False) == ASK


def test_delegated_context_without_a_level_is_not_gated_by_the_new_menu():
    context = ToolRunSecurityContext(delegated_credential=True, access_level=None)
    assert context.decision_for("ask_user").allowed is True

    read_only = ToolRunSecurityContext(
        delegated_credential=True, access_level=READ_ONLY
    )
    # write_file is already blocked for tokens; manage_notes is permitted to
    # them, so only Read Only can refuse its write effect.
    blocked = read_only.decision_for("manage_notes", '{"action":"create"}')
    assert blocked.allowed is False
    assert blocked.approvable is False


def test_read_only_removes_mutating_tools_and_mcp():
    disabled = disabled_tools_for(READ_ONLY)
    assert {"bash", "python", "write_file"} <= disabled
    assert disable_mcp_for(READ_ONLY) is True
    assert disabled_tools_for(ASK) == frozenset()
    assert disable_mcp_for(FULL_ACCESS) is False


@pytest.mark.parametrize(
    "level, tool, expected",
    [
        (READ_ONLY, "read_file", False),
        (ASK, "bash", True),
        (ASK, "send_email", True),
        (ASK, "manage_notes", True),
        (AUTO, "bash", False),
        (AUTO, "ui_control", False),
        (AUTO, "send_email", True),
        (AUTO, "unknown_tool_xyz", True),
        (FULL_ACCESS, "send_email", False),
        (FULL_ACCESS, "unknown_tool_xyz", False),
    ],
)
def test_requires_approval_by_level_and_effect(level, tool, expected):
    capabilities = capabilities_for_action(tool, "{}")
    needs, _reason = requires_approval(
        level, capabilities.effects, known=capabilities.known
    )
    assert needs is expected


# ── Gate decisions ────────────────────────────────────────────────────────


def test_read_only_allows_reads_and_hard_denies_writes():
    context = ToolRunSecurityContext(access_level=READ_ONLY)
    assert context.decision_for("read_file").allowed is True

    denied = context.decision_for("bash")
    assert denied.allowed is False
    # A card could never lift Read Only, so the loop must not offer one.
    assert denied.approvable is False
    assert "Read Only" in (denied.reason or "")


def test_ask_level_denial_is_approvable_by_a_card():
    context = ToolRunSecurityContext(access_level=ASK)
    decision = context.decision_for("bash")
    assert decision.allowed is False
    assert decision.approvable is True


def test_chat_scope_grant_bypasses_ask_but_never_read_only():
    granted = ToolRunSecurityContext(access_level=ASK, approval_gate_bypassed=True)
    assert granted.decision_for("bash").allowed is True

    # Read Only is deployment policy and is checked before any human grant.
    read_only = ToolRunSecurityContext(
        access_level=READ_ONLY, approval_gate_bypassed=True
    )
    assert read_only.decision_for("bash").allowed is False


def test_auto_level_admits_workspace_execution_but_asks_before_egress():
    context = ToolRunSecurityContext(access_level=AUTO)
    assert context.decision_for("bash").allowed is True
    assert context.decision_for("write_file").allowed is True

    egress = context.decision_for("send_email")
    assert egress.allowed is False
    assert egress.approvable is True


def test_full_access_still_honours_token_only_denials():
    delegated = ToolRunSecurityContext(
        access_level=FULL_ACCESS, delegated_credential=True
    )
    decision = delegated.decision_for("bash")
    assert decision.allowed is False
    assert decision.approvable is False


def test_none_keeps_the_legacy_post_external_context_gate():
    # No level, no taint: nothing to gate.
    assert ToolRunSecurityContext().decision_for("bash").allowed is True
    # No level, tainted: the legacy gate arms and its denial is approvable.
    legacy = ToolRunSecurityContext(external_untrusted_context_seen=True)
    decision = legacy.decision_for("bash")
    assert decision.allowed is False
    assert decision.approvable is True


# ── Unsafe launch roots are refused before a card is offered ──────────────


def test_launch_workspace_conflict_reports_server_state(tmp_path, monkeypatch):
    import src.agent_runtime.process_resources as process_resources

    workspace = tmp_path / "project"
    (workspace / "data").mkdir(parents=True)
    control = workspace / "data" / "app.db"
    control.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        process_resources, "_launch_control_paths", lambda: (control,)
    )

    class Root:
        path = workspace

    reason = process_resources.launch_workspace_conflict(Root())
    assert reason and "server state" in reason

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    Root.path = outside
    assert process_resources.launch_workspace_conflict(Root()) is None


# ── Persistence ───────────────────────────────────────────────────────────


def test_session_orm_has_access_level_default():
    column = DbSession.__table__.columns["access_level"]
    assert column.nullable is True
    assert column.default.arg == ASK


def test_chat_session_model_defaults_to_ask_and_accepts_a_level():
    assert Session(
        id="s1", name="Chat", endpoint_url="http://ep", model="m"
    ).access_level == ASK
    assert Session(
        id="s2",
        name="Chat",
        endpoint_url="http://ep",
        model="m",
        access_level=READ_ONLY,
    ).access_level == READ_ONLY


def test_session_manager_round_trips_access_level(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import core.database as database
    import core.session_manager as session_manager_module

    engine = create_engine(f"sqlite:///{tmp_path / 'access.db'}")
    try:
        database.Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        monkeypatch.setattr(session_manager_module, "SessionLocal", factory)

        manager = SessionManager.__new__(SessionManager)
        manager.sessions = {}
        manager.upload_handler = None

        created = manager.create_session(
            "sid",
            "Read only chat",
            "http://localhost/v1/chat/completions",
            "gpt-4",
            owner="alice",
            access_level=READ_ONLY,
        )
        assert created.access_level == READ_ONLY

        db = factory()
        try:
            row = db.query(DbSession).filter(DbSession.id == "sid").first()
            assert row.access_level == READ_ONLY
            hydrated = manager._db_to_session_meta(row)
        finally:
            db.close()

        assert hydrated.access_level == READ_ONLY
    finally:
        engine.dispose()


# ── Exact-approval arming ─────────────────────────────────────────────────


def _armed(level, *, taint=False, approval_taint=False, tool="bash", content="{}"):
    from types import SimpleNamespace

    from src.tool_execution import _exact_approval_armed

    context = ToolRunSecurityContext(
        external_untrusted_context_seen=taint,
        access_level=level,
    )
    approval = SimpleNamespace(
        pending=SimpleNamespace(
            external_untrusted_context_seen=approval_taint,
        )
    )
    block = SimpleNamespace(tool_type=tool, content=content)
    return _exact_approval_armed(context, approval, block)


def test_exact_approval_is_armed_by_ask_level_without_taint():
    assert _armed(ASK) is True


def test_exact_approval_is_armed_by_auto_for_high_risk_effects():
    assert _armed(AUTO, tool="web_fetch") is True


def test_exact_approval_is_not_armed_by_auto_for_workspace_effects():
    # Auto never asks for a workspace write, so no card should be armed for it.
    assert _armed(AUTO, tool="write_file", content='{"path":"a.txt"}') is False


def test_exact_approval_is_not_armed_when_the_level_no_longer_asks():
    # The level was lowered while the card was open; the sealed action must not
    # slip through the gate it was raised under.
    assert _armed(READ_ONLY) is False
    assert _armed(None) is False


def test_exact_approval_stays_armed_for_legacy_taint_at_any_level():
    assert _armed(None, taint=True, approval_taint=True) is True
    assert _armed(READ_ONLY, taint=True, approval_taint=True) is True


def test_exact_approval_is_armed_for_full_access_which_asks_nothing():
    assert _armed(FULL_ACCESS) is True


def test_exact_approval_requires_a_real_security_context():
    from types import SimpleNamespace

    from src.tool_execution import _exact_approval_armed

    approval = SimpleNamespace(
        pending=SimpleNamespace(external_untrusted_context_seen=True)
    )
    block = SimpleNamespace(tool_type="bash", content="{}")
    assert _exact_approval_armed(None, approval, block) is False
