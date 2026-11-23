"""Read-only package update check for the Odysseus desktop helper.

This never installs anything and never runs a privileged command. It reads the
local package manager's cached view of available updates. The result can be
stale until the user refreshes their package manager.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

_TIMEOUT_S = 30


def _which(name: str) -> str | None:
    return shutil.which(name)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_S,
        check=False,
    )


def _result(manager: str, packages: list[str], note: str = "") -> dict[str, Any]:
    return {
        "ok": True,
        "manager": manager,
        "count": len(packages),
        "packages": packages[:20],
        "truncated": len(packages) > 20,
        "note": note
        or "Local cache only. Open your software manager to refresh and install.",
    }


def _arch() -> dict[str, Any] | None:
    if not _which("pacman"):
        return None
    result = _run(["pacman", "-Qu"])
    # pacman -Qu exits 1 when there is nothing to upgrade.
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return _result("pacman", lines)


def _debian() -> dict[str, Any] | None:
    if not _which("apt"):
        return None
    result = _run(["apt", "list", "--upgradable"])
    packages = []
    for line in result.stdout.splitlines():
        text = line.strip()
        if not text or text.startswith("Listing..."):
            continue
        if "/" in text and "upgradable from:" in text:
            packages.append(text.split("/", 1)[0])
    return _result("apt", packages)


def _fedora() -> dict[str, Any] | None:
    if not _which("dnf"):
        return None
    result = _run(["dnf", "-q", "check-update"])
    # dnf returns 100 when updates are available, 0 when none.
    packages = []
    for line in result.stdout.splitlines():
        text = line.strip()
        if not text or text.startswith(("Last metadata", "Obsoleting", "Security:")):
            continue
        parts = text.split()
        if len(parts) >= 2:
            packages.append(parts[0])
    return _result("dnf", packages)


def _suse() -> dict[str, Any] | None:
    if not _which("zypper"):
        return None
    result = _run(["zypper", "--non-interactive", "list-updates"])
    packages = []
    for line in result.stdout.splitlines():
        text = line.strip()
        if not text or text.startswith(("Loading", "Reading", "S |", "---+", "v |")):
            continue
        parts = [part for part in text.split("|") if part.strip()]
        if len(parts) >= 2:
            packages.append(parts[1].strip())
    return _result("zypper", packages)


def check_updates(*, refresh: bool = False) -> dict[str, Any]:
    """Return available updates from the local package cache.

    ``refresh=True`` is deliberately not implemented: refreshing usually needs
    root or a network round-trip. The helper points the user at their software
    manager instead of escalating.
    """
    if refresh:
        return {
            "ok": False,
            "error": "Refreshing the package cache is not supported. Open your software manager to refresh.",
        }
    for detector in (_arch, _debian, _fedora, _suse):
        result = detector()
        if result is not None:
            return result
    return {"ok": False, "error": "No supported package manager found (pacman, apt, dnf, zypper)."}


def _main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Check for available package updates.")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    print(json.dumps(check_updates(refresh=args.refresh), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
