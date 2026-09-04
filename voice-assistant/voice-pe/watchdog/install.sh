#!/usr/bin/env bash
# Install/refresh the Voice PE bridge-audio watchdog on the Beelink as a *user*
# unit under pi (lingering is on; no sudo, no root process) — same shape as
# ma-announce-watchdog/install.sh.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.config/systemd/user"
mkdir -p "$DEST"
install -m 0644 "$SRC/voicepe-watchdog.service" "$DEST/"
install -m 0644 "$SRC/voicepe-watchdog.timer" "$DEST/"
systemctl --user daemon-reload
systemctl --user enable --now voicepe-watchdog.timer
echo
systemctl --user list-timers voicepe-watchdog.timer --no-pager
echo
echo "Logs:  journalctl --user -u voicepe-watchdog.service -f"
echo "State: cat ~/.local/state/voicepe-watchdog/state.json"
