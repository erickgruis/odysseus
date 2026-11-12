#!/bin/bash
# Install Odysseus as a systemd --user service.
#
# Run this from the repository root after creating the venv and running
# setup.py:
#
#   ./install-service.sh
#
# It copies deploy/systemd/odysseus.service to ~/.config/systemd/user/,
# rewrites %h/odysseus when this checkout is not at ~/odysseus, then enables
# and starts the service. Safe to re-run.
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_SRC="$REPO_DIR/deploy/systemd/odysseus.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_DST="$UNIT_DIR/odysseus.service"
DEFAULT_REPO="$HOME/odysseus"

if [ ! -f "$UNIT_SRC" ]; then
  echo "Error: $UNIT_SRC not found." >&2
  exit 1
fi
if [ ! -x "$REPO_DIR/venv/bin/python" ]; then
  echo "Error: $REPO_DIR/venv/bin/python not found." >&2
  echo "Create the venv and install requirements first:" >&2
  echo "  python3 -m venv venv && ./venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

mkdir -p "$UNIT_DIR"

if [ "$REPO_DIR" = "$DEFAULT_REPO" ]; then
  cp "$UNIT_SRC" "$UNIT_DST"
else
  echo "Checkout is at $REPO_DIR (not $DEFAULT_REPO); rewriting unit paths."
  sed "s|%h/odysseus|$REPO_DIR|g" "$UNIT_SRC" > "$UNIT_DST"
fi

systemctl --user daemon-reload
systemctl --user enable --now odysseus
systemctl --user status odysseus --no-pager || true
