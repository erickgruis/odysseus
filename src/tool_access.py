"""Interactive access-level presets for the agent tool loop.

Four levels, modelled on DeepSeek Harness's permission presets, map onto the
mechanisms this server already enforces:

``read_only``
    Plan-mode policy: only the read-only allowlist runs, MCP is dropped, and
    ``bash``/``python`` are blocked outright.  No approval is needed because
    nothing mutating is reachable.

``ask``
    Everything the request policy permits, but every call with a non-read
    effect raises the exact-action approval card first.

``auto``
    Workspace writes, code execution, and UI changes run without a prompt.
    Network egress, external side effects, admin changes, destructive actions,
    and unknown tools still ask.

``full_access``
    No prompts at all.  Deployment policy (owner, delegated credentials, tool
    policy, containment) still applies; this level only removes the approval
    card.

The level never grants a tool by itself.  It is applied *after* the request
policy, so it can only add restrictions or remove prompts for calls that are
already admitted.  A session's level is stored server-side; the browser copy is
a display value.

This module deliberately imports no capability metadata at module scope.  The
effects are plain strings matching :class:`src.tool_capabilities.ToolEffect`
values, which keeps ``tool_capabilities`` free to import this module.
"""

from __future__ import annotations

from typing import Iterable, Optional

READ_ONLY = "read_only"
ASK = "ask"
AUTO = "auto"
FULL_ACCESS = "full_access"

ORDER = (READ_ONLY, ASK, AUTO, FULL_ACCESS)
DEFAULT_LEVEL = ASK

# Effects a read-only turn may perform.  Mirrors the old post-external-context
# gate's read set so the two policies cannot drift.
READ_EFFECTS = frozenset(
    {
        "read_public",
        "read_workspace",
        "read_private",
        "brokered_network_read",
        "user_interaction",
    }
)

# "Auto" is workspace-scoped: these effects still need a human because they
# leave the machine, change deployment state, or destroy data.
AUTO_PROMPT_EFFECTS = frozenset(
    {
        "network_egress",
        "external_side_effect",
        "admin_change",
        "destructive",
    }
)

_LABELS = {
    READ_ONLY: "Read Only",
    ASK: "Ask",
    AUTO: "Auto",
    FULL_ACCESS: "Full Access",
}


def normalize_access_level(value: object, *, default: Optional[str] = DEFAULT_LEVEL) -> Optional[str]:
    """Return a known level id, or *default* when the value is missing/invalid.

    Passing ``default=None`` lets callers distinguish "not set" from an actual
    level, which is how a session row added before this feature falls back to
    the app default instead of silently becoming the safest level.
    """

    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized in ORDER:
        return normalized
    if normalized in {"full", "fullaccess", "unrestricted", "yolo"}:
        return FULL_ACCESS
    if normalized in {"readonly", "read"}:
        return READ_ONLY
    if normalized in {"workspace", "workspace_write", "auto_workspace"}:
        return AUTO
    return default


def clamp_access_level(
    value: object,
    *,
    admin: bool,
    delegated: bool,
    default: str = DEFAULT_LEVEL,
) -> str:
    """Return the strongest level this caller may select in the UI.

    A bearer-token run has no human to answer a card, so it may not select the
    prompting levels; those are kept for display but not enforced (see
    :func:`enforced_level`). Non-admins cannot select ``full_access``; their
    tools are already capped by the non-admin blocklist, so ``auto`` is the
    most permissive useful level.
    """

    level = normalize_access_level(value, default=default) or default
    if delegated:
        return ASK if level == FULL_ACCESS or level == AUTO else level
    if not admin and level == FULL_ACCESS:
        return AUTO
    return level


def enforced_level(value: object, *, delegated: bool) -> Optional[str]:
    """The level a run actually enforces, or None for the legacy gate.

    An API token has no human to answer a card, so an asking level would turn
    every mutating call into an unanswerable prompt. Those runs keep the
    legacy post-external-context gate instead. An explicit Read Only choice is
    a restriction, not a prompt, so it still applies.
    """

    resolved = normalize_access_level(value, default=None)
    if delegated and resolved != READ_ONLY:
        return None
    return resolved


def is_read_only(value: object) -> bool:
    return normalize_access_level(value, default=None) == READ_ONLY


def widen_authority_for_level(
    authority,
    level: object,
    tool_names: Iterable[str] | None = None,
):
    """Grant the tools an interactive level is allowed to reach.

    Request authority used to be derived only from the wording of the latest
    message, which meant an explicit user decision (Ask + approval, or Full
    Access) could not admit a tool the sentence did not mention: the call was
    refused with ``request_authority_denied`` before the approval card could
    help. The level is the user's permission choice, so it now widens the
    grant set:

    * ``read_only`` never widens - its allowlist is a denial, not a grant.
    * ``ask`` grants every offered tool; each mutating call still raises the
      exact-approval card.
    * ``auto`` grants the same set and still prompts for network/destructive
      effects.
    * ``full_access`` grants the same set and prompts for nothing.

    Denials from the tool policy, the caller, containment and ownership are
    applied afterwards by ``RequestAuthority.restrict`` and still win.
    """

    resolved = normalize_access_level(level, default=None)
    if resolved is None or resolved == READ_ONLY:
        return authority

    from src.agent_runtime.authority import OperationGrant
    from src.turn_contract import canonical_tool

    names = [name for name in (tool_names or ()) if name]
    if not names:
        try:
            from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

            names = [
                (schema.get("function") or {}).get("name") or schema.get("name")
                for schema in FUNCTION_TOOL_SCHEMAS
            ]
        except Exception:  # pragma: no cover - defensive import guard
            names = []
        # MCP tools are namespaced (mcp__<server>__<tool>) and no built-in
        # family contains them, so without this they stay denied no matter
        # which level the user picked. Per-chat opt-outs are applied later by
        # RequestAuthority.restrict, so a disabled server still cannot run.
        try:
            from src.tool_utils import get_mcp_manager

            manager = get_mcp_manager()
            if manager is not None:
                names.extend(
                    tool.get("qualified_name")
                    for tool in manager.get_all_tools()
                    if tool.get("qualified_name")
                )
        except Exception:  # pragma: no cover - defensive import guard
            pass

    granted = []
    seen = set()
    for grant in authority.grants:
        canonical = canonical_tool(grant.tool)
        if canonical in seen:
            continue
        seen.add(canonical)
        granted.append(grant if canonical == grant.tool else OperationGrant(canonical))
    for name in names:
        canonical = canonical_tool(name)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        granted.append(OperationGrant(canonical))

    if len(granted) == len(authority.grants):
        return authority
    # Rebuild through ``replace`` with the sealed caches cleared so
    # ``__post_init__`` reseals backend resources, owned scopes and launch
    # scopes for the newly granted tools (a plain grant union would leave
    # bash/python without the launch scope its executor requires).
    from dataclasses import replace

    return replace(
        authority,
        grants=tuple(granted),
        backend_resources=None,
        owned_scopes=None,
        launch_scopes=None,
        job_resources=None,
        browser_sessions=None,
        browser_pages=None,
    )


def label_for(value: object) -> str:
    return _LABELS.get(normalize_access_level(value, default=None) or "", "Ask")


def _effect_values(effects: Iterable[object]) -> frozenset[str]:
    return frozenset(
        str(getattr(effect, "value", effect)) for effect in (effects or ())
    )


def requires_approval(
    level: object,
    effects: Iterable[object],
    *,
    known: bool = True,
) -> tuple[bool, str]:
    """Whether a call with *effects* needs the exact-approval card.

    Returns ``(needs_approval, reason)``.  A read-only level never asks: it
    denies instead, because the tool should not be reachable at all and a card
    would imply the action could be authorized.
    """

    resolved = normalize_access_level(level) or DEFAULT_LEVEL
    values = _effect_values(effects)
    if resolved == READ_ONLY:
        blocked = values - READ_EFFECTS
        if not known or blocked:
            return True, "Read Only access blocks mutating tools."
        return False, ""
    if resolved == FULL_ACCESS:
        return False, ""
    if not known:
        return True, "Unknown tools require approval."
    if resolved == ASK:
        blocked = values - READ_EFFECTS
        if blocked:
            named = ", ".join(sorted(blocked))
            return True, f"This action can {named.replace('_', ' ')}."
        return False, ""
    # AUTO
    blocked = values & AUTO_PROMPT_EFFECTS
    if blocked:
        named = ", ".join(sorted(blocked))
        return True, f"Auto access still asks before {named.replace('_', ' ')}."
    return False, ""


def disabled_tools_for(level: object) -> frozenset[str]:
    """Tool names a level must remove from the request, not merely gate."""

    if not is_read_only(level):
        return frozenset()
    from src.tool_security import plan_mode_disabled_tools

    return frozenset(plan_mode_disabled_tools())


def disable_mcp_for(level: object) -> bool:
    return is_read_only(level)


# Public metadata for the settings UI and API.  Kept server-side so the menu
# copy and the enforcement table cannot disagree.
PRESETS = (
    {
        "id": READ_ONLY,
        "label": _LABELS[READ_ONLY],
        "short": "Inspect only",
        "description": "Read files and search. No writes, shell, or MCP tools.",
        "risk": False,
    },
    {
        "id": ASK,
        "label": _LABELS[ASK],
        "short": "Approve each change",
        "description": "Every write, command, or network action waits for your approval.",
        "risk": False,
    },
    {
        "id": AUTO,
        "label": _LABELS[AUTO],
        "short": "Workspace auto",
        "description": "Runs edits and commands without asking; external actions still ask.",
        "risk": False,
    },
    {
        "id": FULL_ACCESS,
        "label": _LABELS[FULL_ACCESS],
        "short": "No prompts",
        "description": "Runs every permitted action without asking. Use only in trusted work.",
        "risk": True,
    },
)
