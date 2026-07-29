# Music Assistant announcement-lock watchdog

Catches the failure mode where **announcements to a player go silent forever**
while everything else looks healthy: the pushover still arrives, the player still
shows `available: true` and `state: idle`, MA logs nothing, and Node-RED reports
only `[error] [http request:MA API: play final TTS] no response from server`.

## The bug

MA 2.6.3's snapcast provider ends `play_announcement` with an unbounded wait
(`music_assistant/providers/snapcast/__init__.py`):

```python
while stream.status != "idle":
    await asyncio.sleep(0.25)      # no timeout, no connection check
```

If the snapserver control connection drops **while an announcement is in flight**,
the provider reloads and rebuilds its stream objects, but the in-flight coroutine
is still holding the old, now-orphaned `Snapstream`. Its `.status` is never
updated again, so the loop spins forever — inside that player's `asyncio.Lock`,
with `announcement_in_progress` left `True`. Every later announcement to that
player blocks on the lock forever.

The controller (`controllers/players.py`) is not at fault: `announcement_in_progress`
sits in a proper `try/finally`. It never leaked — it never *returned*.

Caught in the logs to the millisecond on 2026-07-26:

```
04:29:09.375 [music_assistant.players]  Playback announcement to player Master Bedroom
04:29:09.376 [music_assistant.snapcast] Connection to SnapServer lost, reason:
                                        [Errno 104] Connection reset by peer.
```

The Loft wedged the same way 4.5 h earlier (2026-07-25 23:50:58). Three nights of
bedtime summaries arrived by pushover with no audio. There have been ~80 snapserver
connection drops since May 2026 (2–5/week), so any drop landing during an
announcement is a live risk.

**Upstream has rewritten this path** (2.9.x replaces the poll with
`ma_stream.wait_for_stopped(timeout_sec)`, awaiting its own streamer task instead
of polling a shared object). Deliberately *not* patched locally: the container
tracks `:latest`, so a hand-patch of a file upstream already deleted would be
silently discarded on the next pull. The fix is the MA upgrade (2.6.3 → 2.9.x);
this watchdog is the safety net until then, and afterwards still covers any other
hang that parks the announcement lock (e.g. a stalled ffmpeg), on any provider.

## What it does

Every 2 minutes:

1. `POST players/all` to the MA API bridge.
2. Any player with `announcement_in_progress: true` gets a first-seen timestamp.
   Real announcements finish in seconds; still flagged after **6 minutes** = wedged.
3. On a confirmed wedge, restart the MA container — **unless music is actually
   playing somewhere**, in which case defer to the next run and alert once.
4. Verify the flags cleared, then pushover the outcome.

Guard rails:

- **Cooldown, 60 min.** A wedge that survives a restart escalates (priority 1)
  instead of restart-looping.
- **API unreachable ≠ wedge.** Never restarts on that; alerts after 5 consecutive misses.
- **Self-healing is normal.** A flag that clears on its own drops out silently.

## Usage

```bash
./install.sh                              # install + enable the user timer
python3 ma_announce_watchdog.py --status  # current flags for every player
python3 ma_announce_watchdog.py --dry-run # detect + alert, never restart
python3 ma_announce_watchdog.py --test-alert
journalctl --user -u ma-announce-watchdog.service -f
```

Runs as a **user** unit (lingering is enabled for `pi`, and `pi` is already in the
`docker` group) — no sudo, no root process poking at containers.

State: `~/.local/state/ma-announce-watchdog/state.json`
Pushover creds: `/home/pi/cecret_lake/pushover/.env` (referenced by path, never copied).

## Manual recovery

```bash
python3 ma_announce_watchdog.py --status   # look for announcement_in_progress=true
docker restart music-assistant-server      # the only thing that clears it
```

Snapserver state, when the group is parked on a dead announcement stream:

```bash
curl -s -X POST http://192.168.10.140:1780/jsonrpc \
  -H 'Content-Type: application/json' \
  -d '{"id":1,"jsonrpc":"2.0","method":"Server.GetStatus"}' | jq '.result.server.groups'
```

A wedged player shows `stream_id: "Music Assistant - <name> (announcement)"` with
that stream `idle`, instead of being back on `default`.
