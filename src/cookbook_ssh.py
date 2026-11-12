"""Where Cookbook keeps its SSH identity.

The Cookbook UI generates one key and uses it for every configured server. Both
the cookbook routes and the agent's ``ssh_exec`` tool must resolve the same
path, otherwise the agent authenticates with a different identity than the
"Test SSH" button the user just used.

Inside the Docker image the key lives under ``/app/.ssh`` (bind-mounted from
``<data>/ssh``). On a normal host it is the user's ``~/.ssh``.
"""

from __future__ import annotations

from pathlib import Path

from core.platform_compat import IS_WINDOWS

COOKBOOK_KEY_NAME = "id_ed25519"


def cookbook_ssh_dir() -> Path:
    """Return the directory holding Cookbook-generated SSH material."""
    if not IS_WINDOWS:
        app_ssh = Path("/app/.ssh")
        if Path("/app").exists():
            return app_ssh
    return Path.home() / ".ssh"


def cookbook_ssh_key_path() -> Path:
    """Return the private key the Cookbook UI generated for its servers."""
    return cookbook_ssh_dir() / COOKBOOK_KEY_NAME