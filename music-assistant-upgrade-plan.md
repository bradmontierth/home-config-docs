# Music Assistant upgrade: 2.6.3 → 2.9.x

**Status:** backlog, not scheduled. Opened 2026-07-29.
**Why it matters:** MA is load-bearing for voice, announcements, the jukebox and
whole-home audio. This is the fix for a real silent-failure bug, but the blast
radius is wide enough that it needs a window and a rehearsed rollback — not a
drive-by `docker compose pull`.

## Why upgrade

MA 2.6.3's snapcast provider ends `play_announcement` with an unbounded wait:

```python
while stream.status != "idle":
    await asyncio.sleep(0.25)      # no timeout, no connection check
```

A snapserver connection drop mid-announcement orphans the `Snapstream` the
coroutine is polling, so it spins forever inside the player's announce lock with
`announcement_in_progress` stuck `True` — **every subsequent announcement to that
player is silently swallowed** until MA restarts. Cost us three nights of silent
bedtime summaries (Master Bedroom 2026-07-26, Loft 2026-07-25). ~80 snapserver
drops since May 2026 make it recurring, not freak.

Upstream rewrote the path in 2.9.x: `ma_stream.wait_for_stopped(timeout_sec)`
awaits its own streamer task instead of polling a shared object, so reconnection
can no longer orphan the wait. Latest release at time of writing: **2.9.9**
(2026-07-17). We run **2.6.3**.

Deliberately *not* patched locally: the container tracks `:latest`, so a
hand-patch of a file upstream has already deleted would be silently discarded on
the next pull. Interim safety net is `home_config/ma-announce-watchdog/`
(detects the wedge, restarts MA, pushovers) — see its README.

## Blast radius: everything that talks to MA

MA listens on `192.168.10.217:8095` (container `music-assistant-server`). Found
2026-07-29 by grepping `/home/pi` for `8095/api`, `players/cmd`, `music_assistant`:

| Consumer | Path | What breaks if the API shifts |
|---|---|---|
| **Node-RED "Amp Speakers" subflow** (`e711d48f74f78209`) | `players/cmd/play_announcement`, isolate bridge `:8461` | every announcement: bedtime, doorbell, broadcast intercom, kitchen messages |
| **Voice orchestrator** | `home_config/voice-assistant/orchestrator/music.py`, `config.py` | voice music control, ducking, wake-over-music |
| **NFC jukebox** | `nfc-jukebox/app/main.py`, `static/app.js` | card scans, play/pause, now-playing popup |
| **home-audio-adapter** | `home-audio-adapter/app/backends/music_assistant.py` | whole-home audio adapter layer |
| **tts-router** | `tts-router/admin_app/main.py` | TTS routing/admin |
| **HA integration** | Music Assistant HA integration | `media_player.*` entities, any HA automation touching them |
| **Immich slideshow audio relay** | via orchestrator `/satellite/play` | video audio to kitchen satellite |
| **Node-RED volume flows** | `home_config/voice-assistant/node-red/deploy_volume_flow.py` | kitchen volume domains |

Known API-shape risks across 3 minor versions: player IDs and the
`ma_<name>` naming (`MA_PLAYER_MAP` in the Amp Speakers subflow hardcodes them),
`players/all` field names (the watchdog keys on `announcement_in_progress`,
`state`, `available`), announcement volume semantics, and the snapcast provider's
stream naming (`Music Assistant - <name> (announcement)`).

## Test pathway (build before upgrading)

The point is a scripted pre/post comparison, so "did the upgrade break anything"
is answered in minutes rather than discovered a week later at bedtime.

1. **Capture a baseline** on 2.6.3: `players/all` for every player (ids, features,
   volume/mute controls), snapserver `Server.GetStatus`, and the HA `media_player.*`
   entity list. Store as JSON in the repo.
2. **Smoke script** — one command, runs pre- and post-upgrade, diffs against baseline:
   - every expected `player_id` still present, `available: true`
   - announcement to a **quiet** zone (loft or shower, not bedrooms) returns 200,
     MA logs `Playback announcement to player X`, and `announcement_in_progress`
     returns to `false` within 60s (the exact regression we're fixing)
   - volume set/restore round-trip
   - a short music play/pause/stop on one zone
   - HA `media_player.*` entities still resolve
3. **Per-consumer checks** — jukebox card scan, one voice music command, a
   doorbell announcement, a bedtime inject (`POST /inject/80b4df73f969bbf6`,
   check-only — it does *not* touch lights), kitchen volume.
4. **Rollback**: pin the current image by digest *before* pulling, so rollback is
   `docker compose up -d` with the old digest. Note the current tag is floating
   `:latest` — capture `docker inspect --format '{{.Image}}' music-assistant-server`
   first. Back up the MA config volume.
5. **Timing**: not at bedtime. Mid-morning, with the watchdog's cooldown in mind
   (it will restart MA if it sees a wedge — consider stopping the timer during
   the upgrade window: `systemctl --user stop ma-announce-watchdog.timer`).

## Open questions

- Does 2.9.x change player id derivation for snapcast clients? If so,
  `MA_PLAYER_MAP` in the Amp Speakers subflow and `phones.json`-style hardcoded
  ids need updating in lockstep.
- Spotify provider: 2.6.3 already throws intermittent `'refresh_token'` errors
  (see [[voice-assistant-project]] notes on the Fern Hill music misses). Check
  whether 2.9.x fixes or worsens that before upgrading, since it's a known sore spot.
