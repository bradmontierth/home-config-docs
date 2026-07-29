#!/usr/bin/env bash
# Install/refresh the MA announcement-lock watchdog on the Beelink (the docker host).
#
# Installed as a *user* unit under pi: lingering is enabled for pi, and the
# script needs only the docker group that pi already has -- so no sudo, and no
# root-owned process poking at containers.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.config/systemd/user"

mkdir -p "$DEST"
install -m 0644 "$SRC/ma-announce-watchdog.service" "$DEST/"
install -m 0644 "$SRC/ma-announce-watchdog.timer" "$DEST/"
systemctl --user daemon-reload
systemctl --user enable --now ma-announce-watchdog.timer

echo
systemctl --user list-timers ma-announce-watchdog.timer --no-pager
echo
echo "Logs:   journalctl --user -u ma-announce-watchdog.service -f"
echo "Status: python3 $SRC/ma_announce_watchdog.py --status"
