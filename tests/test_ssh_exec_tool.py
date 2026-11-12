"""ssh_exec: the agent's server-side SSH path to Cookbook-configured hosts.

The workspace sandbox cannot see ``~/.ssh``, so these tests pin the contract
that matters: only Cookbook-configured hosts are reachable, the Cookbook key is
offered, and the ssh process gets a scrubbed environment.
"""

import asyncio
import json
import os
import shutil

import pytest

from src.agent_tools import ssh_tools
from src.agent_tools.ssh_tools import SshExecTool, SshTargetError

SERVERS = [
    {"name": "Local", "host": "", "port": ""},
    {"name": "Wendy", "host": "wendy@192.168.0.209", "port": "22"},
]


@pytest.fixture
def configured(monkeypatch):
    async def _servers():
        return [dict(server) for server in SERVERS]

    monkeypatch.setattr(ssh_tools, "_configured_servers", _servers)
    monkeypatch.setattr(ssh_tools, "_boundary_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "src.tool_security.owner_is_admin_or_single_user", lambda owner: True
    )
    return _servers


class _FakeProc:
    def __init__(self, *, returncode=0, stdout=b"", stderr=b"", timeout_first=False):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._timeout_first = timeout_first
        self.killed = False
        self.calls = 0

    async def communicate(self):
        self.calls += 1
        if self._timeout_first and self.calls == 1:
            raise asyncio.TimeoutError
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True


def _install_exec(monkeypatch, proc):
    captured = {}

    async def _exec(*argv, **kwargs):
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)
    return captured


@pytest.mark.asyncio
async def test_resolves_cookbook_name_and_host(configured):
    assert await ssh_tools._resolve_target("Wendy", None) == ("wendy@192.168.0.209", "22")
    assert await ssh_tools._resolve_target("wendy@192.168.0.209", None) == (
        "wendy@192.168.0.209",
        "22",
    )
    # Host-only input reuses the configured user.
    assert await ssh_tools._resolve_target("192.168.0.209", None) == (
        "wendy@192.168.0.209",
        "22",
    )
    assert await ssh_tools._resolve_target("Wendy", "2222") == (
        "wendy@192.168.0.209",
        "2222",
    )


@pytest.mark.asyncio
async def test_rejects_unconfigured_host_before_spawn(configured, monkeypatch):
    called = []

    async def _exec(*args, **kwargs):
        called.append(args)
        raise AssertionError("ssh must not start for an unconfigured host")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)
    result = await SshExecTool().execute(
        json.dumps({"host": "someone@10.9.9.9", "command": "id"}),
        {"owner": "admin", "session_id": "s"},
    )
    assert result["exit_code"] == 1
    assert "not a configured Cookbook server" in result["error"]
    assert "Wendy" in result["error"]
    assert called == []


@pytest.mark.asyncio
async def test_rejects_local_targets(configured):
    for host in ("localhost", "127.0.0.1", "local", ""):
        result = await SshExecTool().execute(
            json.dumps({"host": host, "command": "id"}),
            {"owner": "admin", "session_id": "s"},
        )
        assert result["exit_code"] == 1
        assert "remote cookbook server" in result["error"].lower()


@pytest.mark.asyncio
async def test_requires_admin_owner(configured, monkeypatch):
    monkeypatch.setattr(
        "src.tool_security.owner_is_admin_or_single_user", lambda owner: False
    )
    result = await SshExecTool().execute(
        json.dumps({"host": "Wendy", "command": "id"}),
        {"owner": "regular-user", "session_id": "s"},
    )
    assert result["blocked"] is True
    assert "admin access" in result["error"]


@pytest.mark.asyncio
async def test_runs_ssh_with_cookbook_key_and_scrubbed_env(
    configured, monkeypatch, tmp_path
):
    key = tmp_path / "id_ed25519"
    key.write_text("private", encoding="utf-8")
    monkeypatch.setattr("src.cookbook_ssh.cookbook_ssh_key_path", lambda: key)
    monkeypatch.setenv("ODYSSEUS_TEST_SECRET", "must-not-leak")
    proc = _FakeProc(returncode=0, stdout=b"ok\n", stderr=b"")
    captured = _install_exec(monkeypatch, proc)

    result = await SshExecTool().execute(
        json.dumps({"host": "Wendy", "command": "echo ok; whoami"}),
        {"owner": "admin", "session_id": "s"},
    )

    argv = captured["argv"]
    assert argv[0].endswith("ssh") or argv[0] == shutil.which("ssh")
    assert argv[argv.index("-o") + 1] == "BatchMode=yes"
    assert "StrictHostKeyChecking=accept-new" in argv
    assert argv[argv.index("-i") + 1] == str(key)
    assert argv[-2:] == ["wendy@192.168.0.209", "echo ok; whoami"]
    env = captured["kwargs"]["env"]
    assert "ODYSSEUS_TEST_SECRET" not in env
    assert env["HOME"]
    assert result["stdout"] == "ok\n"
    assert result["stderr"] == ""
    assert result["exit_code"] == 0
    assert result["host"] == "wendy@192.168.0.209"


@pytest.mark.asyncio
async def test_reports_remote_failure(configured, monkeypatch):
    proc = _FakeProc(returncode=255, stdout=b"", stderr=b"Permission denied (publickey)")
    _install_exec(monkeypatch, proc)

    result = await SshExecTool().execute(
        json.dumps({"host": "Wendy", "command": "true"}),
        {"owner": "admin", "session_id": "s"},
    )

    assert result["exit_code"] == 255
    assert "Permission denied" in result["stderr"]


@pytest.mark.asyncio
async def test_timeout_kills_ssh_and_reports_124(configured, monkeypatch):
    proc = _FakeProc(returncode=0, timeout_first=True)
    _install_exec(monkeypatch, proc)

    result = await SshExecTool().execute(
        json.dumps({"host": "Wendy", "command": "sleep 999", "timeout": 1}),
        {"owner": "admin", "session_id": "s"},
    )

    assert proc.killed is True
    assert result["timed_out"] is True
    assert result["exit_code"] == 124
    assert "timed out" in result["error"]


@pytest.mark.asyncio
async def test_invalid_arguments_are_refused(configured):
    tool = SshExecTool()
    ctx = {"owner": "admin", "session_id": "s"}

    for payload, needle in (
        ("not json", "JSON object"),
        (json.dumps({"host": "Wendy"}), "command"),
        (json.dumps({"command": "id"}), "configured remote cookbook server"),
        (json.dumps({"host": "-bad", "command": "id"}), "invalid remote_host"),
        (json.dumps({"host": "Wendy", "command": "id", "ssh_port": "nope"}), "ssh_port"),
        (json.dumps({"host": "Wendy", "command": "id", "timeout": "soon"}), "timeout"),
    ):
        result = await tool.execute(payload, ctx)
        assert result["exit_code"] == 1
        assert needle.lower() in result["error"].lower()


@pytest.mark.asyncio
async def test_no_configured_servers_is_refused(monkeypatch):
    async def _empty():
        return []

    monkeypatch.setattr(ssh_tools, "_configured_servers", _empty)
    monkeypatch.setattr(
        "src.tool_security.owner_is_admin_or_single_user", lambda owner: True
    )
    result = await SshExecTool().execute(
        json.dumps({"host": "Wendy", "command": "id"}),
        {"owner": "admin", "session_id": "s"},
    )
    assert result["exit_code"] == 1
    assert "No remote Cookbook servers" in result["error"]


@pytest.mark.asyncio
async def test_spawn_failure_is_reported(configured, monkeypatch):
    async def _exec(*args, **kwargs):
        raise OSError("ssh missing")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)
    result = await SshExecTool().execute(
        json.dumps({"host": "Wendy", "command": "id"}),
        {"owner": "admin", "session_id": "s"},
    )
    assert result["exit_code"] == 1
    assert "could not start ssh" in result["error"]


def test_tool_is_registered_everywhere_the_agent_needs_it():
    from src.agent_tools import TOOL_HANDLERS
    from src.tool_capabilities import KNOWN_CAPABILITY_TOOLS
    from src.tool_index import ALWAYS_AVAILABLE, BUILTIN_TOOL_DESCRIPTIONS
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS, function_call_to_tool_block
    from src.tool_security import NON_ADMIN_BLOCKED_TOOLS, plan_mode_disabled_tools
    from src.tool_types import TOOL_TAGS
    from src.turn_contract import FAMILY_TOOLS

    schema_names = {schema["function"]["name"] for schema in FUNCTION_TOOL_SCHEMAS}
    assert "ssh_exec" in schema_names
    assert "ssh_exec" in TOOL_TAGS
    assert "ssh_exec" in TOOL_HANDLERS
    assert "ssh_exec" in KNOWN_CAPABILITY_TOOLS
    assert "ssh_exec" in ALWAYS_AVAILABLE
    assert "ssh_exec" in BUILTIN_TOOL_DESCRIPTIONS
    assert "ssh_exec" in NON_ADMIN_BLOCKED_TOOLS
    assert "ssh_exec" in plan_mode_disabled_tools()
    assert "ssh_exec" in FAMILY_TOOLS["shell_files"]

    block = function_call_to_tool_block(
        "ssh_exec", json.dumps({"host": "Wendy", "command": "echo ok"})
    )
    assert block is not None
    assert block.tool_type == "ssh_exec"
    assert json.loads(block.content) == {"host": "Wendy", "command": "echo ok"}


def test_ssh_exec_is_admin_only_at_dispatch():
    from src.tool_execution import _ADMIN_TOOLS

    assert "ssh_exec" in _ADMIN_TOOLS


@pytest.mark.asyncio
async def test_dispatch_blocks_non_admin(tmp_path, monkeypatch):
    from src.agent_runtime.authority import OperationGrant, RequestAuthority
    from src.tool_execution import NO_TOOL_SECURITY_CONTEXT, execute_tool_block
    from src.tool_types import ToolBlock

    monkeypatch.setattr("src.tool_execution._owner_is_admin", lambda owner: False)
    authority = RequestAuthority(
        "r", "regular", "s", str(tmp_path), (OperationGrant("ssh_exec"),)
    )
    _, result = await execute_tool_block(
        ToolBlock("ssh_exec", json.dumps({"host": "Wendy", "command": "id"})),
        owner="regular",
        session_id="s",
        workspace=str(tmp_path),
        request_authority=authority,
        security_context=NO_TOOL_SECURITY_CONTEXT,
    )
    assert result["exit_code"] == 1
    assert "requires an admin user" in result["error"]


@pytest.mark.asyncio
async def test_dispatch_runs_ssh_exec_for_admin(tmp_path, monkeypatch):
    from src.agent_runtime.authority import OperationGrant, RequestAuthority
    from src.tool_execution import NO_TOOL_SECURITY_CONTEXT, execute_tool_block
    from src.tool_types import ToolBlock

    async def _servers():
        return [{"name": "Wendy", "host": "wendy@192.168.0.209", "port": "22"}]

    monkeypatch.setattr(ssh_tools, "_configured_servers", _servers)
    monkeypatch.setattr(ssh_tools, "_boundary_record", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.tool_execution._owner_is_admin", lambda owner: True)
    monkeypatch.setattr(
        "src.tool_security.owner_is_admin_or_single_user", lambda owner: True
    )
    proc = _FakeProc(returncode=0, stdout=b"ok\n")
    _install_exec(monkeypatch, proc)

    authority = RequestAuthority(
        "r", "admin", "s", str(tmp_path), (OperationGrant("ssh_exec"),)
    )
    desc, result = await execute_tool_block(
        ToolBlock("ssh_exec", json.dumps({"host": "Wendy", "command": "echo ok"})),
        owner="admin",
        session_id="s",
        workspace=str(tmp_path),
        request_authority=authority,
        security_context=NO_TOOL_SECURITY_CONTEXT,
    )
    assert result["exit_code"] == 0, result
    assert result["stdout"] == "ok\n"
    assert desc.startswith("registry: ssh_exec")