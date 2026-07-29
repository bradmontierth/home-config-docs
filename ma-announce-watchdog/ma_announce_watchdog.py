#!/usr/bin/env python3
"""Detect and clear wedged Music Assistant announcement locks.

Background
----------
MA 2.6.3's snapcast provider ends `play_announcement` with an unbounded wait:

    while stream.status != "idle":
        await asyncio.sleep(0.25)

If the snapserver control connection drops while an announcement is in flight
(80 such drops since May 2026, ~2-5/week), the provider reloads and rebuilds its
stream objects, but the in-flight coroutine keeps polling the now-orphaned
`Snapstream`. Its `.status` never updates again, so the loop spins forever while
holding that player's `asyncio.Lock` and leaving `announcement_in_progress` set.
Every later announcement to that player blocks on the lock forever -- silently.

That is what happened on 2026-07-26 04:29:09 (Master Bedroom) and 2026-07-25
23:50:58 (Loft): bedtime summaries kept arriving by pushover with no audio for
three nights. Only a restart of the MA container clears it.

Upstream has since rewritten this path (2.9.x: `ma_stream.wait_for_stopped()`
awaits its own streamer task instead of polling a shared object), so this
watchdog is a safety net until the MA upgrade lands -- and afterwards it still
covers any other hang that parks the per-player announcement lock.

Behaviour
---------
Every run: poll MA for all players. A player whose `announcement_in_progress`
stays true across polls for longer than --wedge-minutes is considered wedged
(real announcements finish in seconds). On confirmation, restart the MA
container -- unless something is actively playing music, in which case defer and
retry next run -- then verify the flags cleared and send a pushover.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MA_API = os.environ.get("MA_API", "http://192.168.10.217:8095/api")
CONTAINER = os.environ.get("MA_CONTAINER", "music-assistant-server")
PUSHOVER_ENV = Path(os.environ.get("PUSHOVER_ENV", "/home/pi/cecret_lake/pushover/.env"))
STATE_FILE = Path(
    os.environ.get("STATE_FILE", "/home/pi/.local/state/ma-announce-watchdog/state.json")
)

# A genuine announcement (wake chime + TTS + pad) runs well under a minute.
DEFAULT_WEDGE_MINUTES = 6.0
# Never restart more often than this, so a restart that fails to clear the wedge
# escalates to a human instead of looping.
DEFAULT_COOLDOWN_MINUTES = 60.0


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}", flush=True)


def ma_command(command: str, args: dict | None = None, timeout: int = 10):
    """Call the MA API bridge. Returns parsed JSON, or raises."""
    body = json.dumps(
        {"message_id": f"watchdog-{int(time.time())}", "command": command, "args": args or {}}
    ).encode()
    req = urllib.request.Request(
        MA_API, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode() or "null")


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_FILE)


def pushover(title: str, message: str, priority: int = 0) -> None:
    """Best-effort alert; never let a notification failure mask the real event.

    Targets PUSHOVER_DEVICE from the shared cecret_lake env so this alert lands on
    one phone rather than every device on the account. That file is the single
    source of truth for the device name -- a phone upgrade should be one edit
    there, not a hunt through every project. Never hardcode a device name as a
    fallback: silently broadcasting to all devices is the better failure than
    silently notifying a phone that no longer exists.
    """
    try:
        env = {}
        for line in PUSHOVER_ENV.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
        fields = {
            "token": env["PUSHOVER_API"],
            "user": env["PUSHOVER_USER"],
            "title": title,
            "message": message,
            "priority": priority,
        }
        if env.get("PUSHOVER_DEVICE"):
            fields["device"] = env["PUSHOVER_DEVICE"]
        else:
            log("WARN PUSHOVER_DEVICE unset; alert goes to all devices")
        payload = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request("https://api.pushover.net/1/messages.json", data=payload)
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        log(f"pushover sent: {title}")
    except Exception as exc:  # noqa: BLE001 - alerting must never crash the watchdog
        log(f"WARN pushover failed: {exc!r}")


def restart_ma() -> bool:
    log(f"restarting container {CONTAINER}")
    try:
        subprocess.run(
            ["docker", "restart", CONTAINER], check=True, capture_output=True, timeout=120
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        log(f"ERROR docker restart failed: {exc!r}")
        return False
    # Wait for the API to answer again before declaring success.
    for _ in range(30):
        time.sleep(5)
        try:
            players = ma_command("players/all")
            if players:
                log("MA API back up")
                return True
        except Exception:  # noqa: BLE001 - still coming up
            continue
    log("ERROR MA API did not come back within 150s")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wedge-minutes", type=float, default=DEFAULT_WEDGE_MINUTES)
    ap.add_argument("--cooldown-minutes", type=float, default=DEFAULT_COOLDOWN_MINUTES)
    ap.add_argument(
        "--dry-run", action="store_true", help="detect and alert, but never restart"
    )
    ap.add_argument(
        "--status", action="store_true", help="print current player flags and exit"
    )
    ap.add_argument(
        "--test-alert", action="store_true", help="send a sample pushover and exit"
    )
    args = ap.parse_args()

    if args.test_alert:
        pushover(
            "MA watchdog test",
            "This is a test alert from the Music Assistant announcement-lock watchdog. "
            "If you can read this, the alert path works.",
        )
        return 0

    now = time.time()
    state = load_state()
    pending = state.get("pending", {})

    try:
        players = ma_command("players/all")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # MA being unreachable is a different failure (and self-evident elsewhere);
        # count it but don't restart on it.
        misses = state.get("api_misses", 0) + 1
        state["api_misses"] = misses
        save_state(state)
        log(f"WARN MA API unreachable ({exc!r}) [consecutive={misses}]")
        if misses == 5:
            pushover(
                "Music Assistant unreachable",
                f"MA API at {MA_API} has not answered for {misses} consecutive checks.",
                priority=1,
            )
        return 0
    state["api_misses"] = 0

    if not isinstance(players, list):
        log(f"WARN unexpected players/all payload: {type(players).__name__}")
        return 0

    if args.status:
        for p in players:
            log(
                f"{p.get('player_id'):<28} state={p.get('state'):<8} "
                f"announcement_in_progress={p.get('announcement_in_progress')} "
                f"available={p.get('available')}"
            )
        return 0

    music_playing = [
        p.get("player_id")
        for p in players
        if p.get("state") == "playing" and not p.get("announcement_in_progress")
    ]

    wedged, still_pending = [], {}
    for p in players:
        pid = p.get("player_id")
        if not p.get("announcement_in_progress"):
            continue
        first_seen = pending.get(pid, now)
        still_pending[pid] = first_seen
        held_min = (now - first_seen) / 60.0
        if held_min >= args.wedge_minutes:
            wedged.append((pid, p.get("name", pid), held_min))
        else:
            log(f"{pid}: announcement in progress {held_min:.1f}m (below threshold)")

    state["pending"] = still_pending

    if not wedged:
        save_state(state)
        return 0

    names = ", ".join(f"{name} ({held:.0f}m)" for _, name, held in wedged)
    log(f"WEDGE DETECTED: {names}")

    if args.dry_run:
        save_state(state)
        pushover("MA announcement wedge (dry run)", f"Would restart MA. Wedged: {names}")
        return 0

    if music_playing:
        log(f"deferring restart, music playing on: {', '.join(music_playing)}")
        save_state(state)
        if not state.get("deferred_alerted"):
            state["deferred_alerted"] = True
            save_state(state)
            pushover(
                "MA announcement wedge (restart deferred)",
                f"Wedged: {names}\nHolding off while music plays on {', '.join(music_playing)}. "
                "Will retry on the next check.",
            )
        return 0
    state.pop("deferred_alerted", None)

    last_restart = state.get("last_restart", 0)
    since_restart_min = (now - last_restart) / 60.0
    if since_restart_min < args.cooldown_minutes:
        log(f"in cooldown ({since_restart_min:.0f}m since last restart), not restarting")
        save_state(state)
        if not state.get("cooldown_alerted"):
            state["cooldown_alerted"] = True
            save_state(state)
            pushover(
                "MA announcement wedge PERSISTS after restart",
                f"Wedged: {names}\nA restart {since_restart_min:.0f}m ago did not clear it. "
                "Needs a look -- announcements to these players are silently dropped.",
                priority=1,
            )
        return 1
    state.pop("cooldown_alerted", None)

    ok = restart_ma()
    state["last_restart"] = time.time()
    state["pending"] = {}
    save_state(state)

    if not ok:
        pushover(
            "MA restart FAILED",
            f"Wedged: {names}\nThe container restart did not complete. Whole-home "
            "announcements are down until this is fixed.",
            priority=1,
        )
        return 1

    # Confirm the flags actually cleared.
    try:
        after = ma_command("players/all")
        leftover = [
            p.get("name", p.get("player_id"))
            for p in after
            if p.get("announcement_in_progress")
        ]
    except Exception:  # noqa: BLE001
        leftover = ["<verification failed>"]

    if leftover:
        pushover(
            "MA restarted but still wedged",
            f"Was: {names}\nStill flagged after restart: {', '.join(leftover)}",
            priority=1,
        )
        return 1

    pushover(
        "Music Assistant announcement wedge cleared",
        f"Wedged: {names}\nRestarted {CONTAINER}; all announcement locks are clear. "
        "Bedroom/loft announcements work again.",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
