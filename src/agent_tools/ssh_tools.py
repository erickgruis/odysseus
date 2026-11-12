"""Remote shell execution over the Cookbook SSH identity.

Why this exists
---------------
Agent shell commands run inside a bubblewrap namespace that mounts a private
root plus the workspace: ``/home`` (and therefore ``~/.ssh``) is deliberately
not visible, and ``HOME`` is remapped to the workspace. A model-driven
``ssh host ...`` from ``bash`` therefore finds no key at all.

The Cookbook "Test SSH" button works because the cookbook routes run ``ssh`` in
the server process. This tool gives the agent that same auth path without
widening the sandbox: ssh runs server-side with the Cookbook key, and the only
reachable destinations are the servers the user configured in Cookbook.

Local execution is intentionally not offered. ``ssh localhost`` would be a
sandbox escape; local commands belong to ``bash``/``python``, which stay
contained.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

from src.tool_utils import _truncate

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 60
MAX_TIMEOUT_S = 300
_CONNECT_TIMEOUT_S = 10
_MAX_COMMAND_CHARS = 20_000

_LOCAL_NAMES = {"", "local", "localhost", "127.0.0.1", "::1", "this machine", "here"}


class SshTargetError(ValueError):
    """The requested host/arguments are not an admitted Cookbook SSH target."""


def _parse_args(content: str) -> dict[str, Any]:
    raw = str(content or "").strip()
    if not raw:
        raise SshTargetError("ssh_exec requires JSON arguments with `host` and `command`.")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise SshTargetError(
            "ssh_exec arguments must be a JSON object, e.g. "
            '{"host": "wendy@192.168.0.209", "command": "uname -a"}'
        ) from error
    if not isinstance(value, dict):
        raise SshTargetError("ssh_exec arguments must be a JSON object.")
    return value


def _split_host(value: str) -> tuple[str, str]:
    """Split ``user@host`` into (user, host). Host-only input yields ("", host)."""
    value = str(value or "").strip()
    if "@" not in value:
        return "", value
    user, _, host = value.rpartition("@")
    return user, host


def _is_local(value: str) -> bool:
    lowered = str(value or "").strip().casefold()
    if lowered in _LOCAL_NAMES:
        return True
    _, host = _split_host(lowered)
    return host in _LOCAL_NAMES


async def _configured_servers() -> list[dict[str, Any]]:
    """The user's Cookbook servers, which define the SSH allowlist."""
    try:
        # Reuse the same source the Cookbook tools read.
        from src.tools.cookbook import _cookbook_servers

        data = await _cookbook_servers()
        hosts = data.get("hosts") if isinstance(data, dict) else None
        if isinstance(hosts, list) and hosts:
            return [row for row in hosts if isinstance(row, dict)]
    except Exception as error:  # noqa: BLE001 - fall back to the state file
        logger.debug("ssh_exec: cookbook server lookup failed: %s", error)

    try:
        from src.constants import COOKBOOK_STATE_FILE

        state = json.loads(Path(COOKBOOK_STATE_FILE).read_text(encoding="utf-8"))
        env = state.get("env") if isinstance(state, dict) else None
        servers = env.get("servers") if isinstance(env, dict) else None
        if isinstance(servers, list):
            return [
                {
                    "name": row.get("name") or "",
                    "host": row.get("host") or "",
                    "port": row.get("sshPort") or row.get("port") or "",
                }
                for row in servers
                if isinstance(row, dict)
            ]
    except (OSError, ValueError, TypeError) as error:
        logger.debug("ssh_exec: cookbook state fallback failed: %s", error)
    return []


def _descriptions(servers: list[dict[str, Any]]) -> str:
    labels = []
    for server in servers:
        host = str(server.get("host") or "").strip()
        if not host:
            continue
        name = str(server.get("name") or "").strip()
        labels.append(f"{name} ({host})" if name else host)
    return ", ".join(labels)


def _validate_target(value: str) -> str:
    from fastapi import HTTPException
    from routes._validators import validate_remote_host

    try:
        validated = validate_remote_host(str(value or "").strip())
    except HTTPException as error:
        raise SshTargetError(str(error.detail)) from error
    return validated or ""


def _validate_port(value: Any) -> str:
    from fastapi import HTTPException
    from routes._validators import validate_ssh_port

    try:
        return validate_ssh_port(str(value or "").strip()) or ""
    except HTTPException as error:
        raise SshTargetError(str(error.detail)) from error


async def _resolve_target(raw_host: Any, explicit_port: Any) -> tuple[str, str]:
    """Map a Cookbook name or configured host to an admitted ``user@host``.

    Only hosts already configured in Cookbook are reachable. Anything else is
    refused before a process is spawned, so a prompt cannot turn this tool into
    a generic SSH client against arbitrary addresses.
    """
    requested = _validate_target(str(raw_host or "").strip())
    if not requested:
        raise SshTargetError(
            "ssh_exec needs a configured remote Cookbook server. "
            "Use bash for local commands."
        )
    if _is_local(requested):
        raise SshTargetError(
            "ssh_exec only reaches remote Cookbook servers; use bash for this machine."
        )

    servers = await _configured_servers()
    remote_servers = [
        server for server in servers if str(server.get("host") or "").strip()
    ]
    if not remote_servers:
        raise SshTargetError(
            "No remote Cookbook servers are configured. Add the machine in Cookbook "
            "first, then authorize its SSH key."
        )

    lowered = requested.casefold()
    requested_user, requested_host = _split_host(requested)
    chosen: tuple[str, str] | None = None
    for server in remote_servers:
        configured_host = str(server.get("host") or "").strip()
        name = str(server.get("name") or "").strip()
        _, configured_hostname = _split_host(configured_host)
        port = str(server.get("port") or server.get("sshPort") or "").strip()
        if (
            (name and name.casefold() == lowered)
            or configured_host.casefold() == lowered
        ):
            chosen = (configured_host, port)
            break
        if configured_hostname.casefold() == requested_host.casefold():
            # Same machine. Keep an explicit user the caller supplied; otherwise
            # use the user the Cookbook entry was configured with.
            chosen = (requested if requested_user else configured_host, port)
            break
    if chosen is None:
        known = _descriptions(remote_servers) or "none"
        raise SshTargetError(
            f"{requested!r} is not a configured Cookbook server. "
            f"Configured remote servers: {known}."
        )

    target, configured_port = chosen
    target = _validate_target(target)
    if not target:
        raise SshTargetError(
            "ssh_exec only reaches remote Cookbook servers; use bash for this machine."
        )
    if explicit_port:
        port = _validate_port(explicit_port)
    else:
        port = _validate_port(configured_port) if configured_port else ""
    return target, port


def _validate_command(value: Any) -> str:
    command = str(value or "").strip()
    if not command:
        raise SshTargetError("ssh_exec requires a non-empty `command`.")
    if "\x00" in command:
        raise SshTargetError("ssh_exec command must not contain NUL bytes.")
    if len(command) > _MAX_COMMAND_CHARS:
        raise SshTargetError(
            f"ssh_exec command is too long ({len(command)} > {_MAX_COMMAND_CHARS} chars)."
        )
    return command


def _validate_timeout(value: Any) -> int:
    if value is None or value == "":
        return DEFAULT_TIMEOUT_S
    try:
        timeout = int(value)
    except (TypeError, ValueError) as error:
        raise SshTargetError("ssh_exec timeout must be an integer number of seconds.") from error
    return max(1, min(timeout, MAX_TIMEOUT_S))


def _ssh_environment() -> dict[str, str]:
    """A minimal environment for the ssh client.

    Deliberately not the server environment: it holds API keys and other
    secrets a remote shell has no business inheriting. ``HOME`` is the server
    user's home so OpenSSH can read ``~/.ssh/config`` and ``known_hosts``.
    """
    env = {
        "PATH": os.environ.get("PATH") or os.defpath or "/usr/local/bin:/usr/bin:/bin",
        "HOME": os.path.expanduser("~") or os.environ.get("HOME", ""),
        "LANG": os.environ.get("LANG") or "C.UTF-8",
    }
    for key in (
        "SSH_AUTH_SOCK",
        "USERPROFILE",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
    ):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def _ssh_argv(target: str, port: str, command: str) -> list[str]:
    ssh = shutil.which("ssh")
    if not ssh:
        raise SshTargetError("The Odysseus host has no ssh client installed.")

    argv = [
        ssh,
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={_CONNECT_TIMEOUT_S}",
        # A first connection to a configured server is expected; remember the
        # key rather than failing or prompting interactively.
        "-o", "StrictHostKeyChecking=accept-new",
    ]
    try:
        from src.cookbook_ssh import cookbook_ssh_key_path

        key_path = cookbook_ssh_key_path()
        if key_path.is_file():
            # Try the Cookbook identity first. No IdentitiesOnly: a host that
            # the user pinned to another key in ~/.ssh/config still works.
            argv.extend(["-i", str(key_path)])
    except OSError as error:
        logger.debug("ssh_exec: cookbook key lookup failed: %s", error)
    if port and port != "22":
        argv.extend(["-p", port])
    argv.extend([target, command])
    return argv


def _boundary_record(
    ctx: dict[str, Any], target: str, port: str, wall_clock_s: int
) -> dict[str, Any] | None:
    """An honest containment record: the remote effect leaves this backend."""
    try:
        from src import containment
        from src.tool_execution import agent_cwd

        spec = containment.agent_spec(agent_cwd(), {}, int(wall_clock_s))
        grant = containment.declare_external_bridge(
            spec,
            owner=str(ctx.get("session_id") or ctx.get("owner") or "ssh_exec"),
            endpoint=f"ssh://{target}:{port or 22}",
        )
        boundary = grant.to_dict()
        boundary["executed"] = False
        return boundary
    except Exception as error:  # noqa: BLE001 - bookkeeping must not block the call
        logger.warning("ssh_exec: could not record containment boundary: %s", error)
        return None


class SshExecTool:
    """Run one shell command on a configured Cookbook server over SSH."""

    async def execute(self, content: str, ctx: dict) -> dict:
        ctx = ctx if isinstance(ctx, dict) else {}
        from src.tool_security import owner_is_admin_or_single_user

        if not owner_is_admin_or_single_user(ctx.get("owner")):
            return {
                "error": "ssh_exec: admin access is required to reach configured remote servers",
                "exit_code": 1,
                "blocked": True,
            }

        try:
            args = _parse_args(content)
            command = _validate_command(args.get("command"))
            timeout = _validate_timeout(args.get("timeout"))
            target, port = await _resolve_target(args.get("host"), args.get("ssh_port"))
            argv = _ssh_argv(target, port, command)
        except SshTargetError as error:
            return {"error": f"ssh_exec: {error}", "exit_code": 1}
        except (OSError, ValueError, TypeError) as error:
            return {"error": f"ssh_exec: {error}", "exit_code": 1}

        boundary = _boundary_record(ctx, target, port, timeout)
        started = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_ssh_environment(),
            )
        except (OSError, ValueError) as error:
            if boundary is not None:
                boundary["executed"] = False
            return {
                "error": f"ssh_exec: could not start ssh: {error}",
                "exit_code": 1,
                **({"containment": boundary} if boundary else {}),
            }

        if boundary is not None:
            boundary["executed"] = True

        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            proc.kill()
            try:
                await asyncio.wait_for(proc.communicate(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                pass
            stdout, stderr = b"", b""
        except asyncio.CancelledError:
            proc.kill()
            raise

        elapsed_ms = int((time.monotonic() - started) * 1000)
        result: dict[str, Any] = {
            "host": target,
            "stdout": _truncate(stdout.decode("utf-8", errors="replace")),
            "stderr": _truncate(stderr.decode("utf-8", errors="replace")),
            "exit_code": proc.returncode if proc.returncode is not None else 1,
            "duration_ms": elapsed_ms,
        }
        if port and port != "22":
            result["ssh_port"] = port
        if timed_out:
            result["timed_out"] = True
            result["exit_code"] = 124
            result["error"] = f"ssh_exec: command timed out after {timeout}s on {target}"
        if boundary is not None:
            result["containment"] = boundary
        return result