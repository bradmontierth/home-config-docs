# Music Assistant upgrade: 2.6.3 → 2.10.x

**Status:** backlog, not scheduled. Opened 2026-07-29. **Interim local patch LIVE on 2.6.3 since 2026-10-20** (see below). **Blocked on a Home Assistant upgrade** (see below).
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

## 2026-10-20: second reason, the snapserver segfault, and target is now 2.10.7

Latest stable is **2.10.7** (2026-10-17); 2.11 is in nightly betas. Retarget
the upgrade at 2.10.7.

**The crash.** snapserver 0.35.0 (latest Snapcast release, so no Snapcast
update fixes it) SIGSEGVs inside `Stream.RemoveStream`: 5 times in 30 days,
3 on 2026-10-19 (07:57 Simon Room, 21:20 and 21:37 Shield). Every zone drops
~2 s and very recent client settings can be lost. Log sequence before each
crash: `Stream.RemoveStream("Music Assistant - X")` -> `playing => idle` ->
`AsioStream ... Operation canceled` -> `Stop X` twice -> SEGV. No matching
upstream issue found.

**Why 2.6.3 triggers it.** `cmd_stop` (snapcast `__init__.py`) does:
1. `group.set_stream("default")`
2. `_delete_stream(music stream)` = `Stream.RemoveStream` **while MA's
   ffmpeg is still connected to the stream's TCP source and writing**
3. only then cancels the stream task.
Every stop and every "replace" (play_media cancels and re-creates) removes a
live TCP source.

**What 2.10.7 does instead** (`providers/snapcast/player.py`, `ma_stream.py`,
`provider.py`, compared side by side, copies in /home/pi/tmp/ma-compare/):
- `stop()` = `set_stream("default")` + `request_stop_stream()`, then returns.
  **No RemoveStream on stop.**
- Queue-backed music streams get a stable name (`Music Assistant -
  <queue id>`) with `destroy_on_stop = False`: the Snapcast source persists
  across plays and is reused; only the feeder stops and restarts.
- A stream no group is using gets `request_stop_stream` after 3 s (the
  feeder stops; the source stays).
- Removal happens only in `destroy()`, which does `request_stop_stream()` ->
  `wait_for_stopped()` -> then `_remove_snap_source()` (groups moved to
  default first). Announcement/hashed streams self-destroy from
  `_on_streamer_done`, i.e. after the feeder has finished. Provider unload
  stops players first, then destroys.
- The one remaining "remove without stopping" is adopting an orphaned stream
  left from an earlier MA run (no feeder attached).
So the remove-a-live-TCP-source pattern is gone from normal playback. That
matches the crash signature; it is a strong inference, not a reproduced
proof (reproducing it would mean deliberately crashing whole-home audio).

**Reproduced 2026-10-20 09:47 on 2.6.3** with `ma-upgrade-check/ma_check.py
smoke --audio`: a quiet play + stop on Simon Room crashed the snapserver
(SEGV count 5 -> 6). Same run: the announcement stream, removed after its
feeder had finished, did not crash. So the trigger is removing a stream
whose feeder is still connected, exactly what 2.10.7 no longer does.
Pre-upgrade baseline: `ma-upgrade-check/snapshots/pre-upgrade.json`.

### 2026-10-20: blocked on Home Assistant, interim patch applied instead

**Why not upgrade now.** HA 2025.11.3 bundles `music-assistant-client`
1.2.4 = API schema **26**. Every MA release after 2.6.x requires a client of
at least schema **28** (`MIN_SCHEMA_VERSION = 28` from 2.7.0 through
2.10.7), so any MA upgrade would disconnect HA's Music Assistant
integration. That breaks Node-RED flows that use those HA entities:
Doorbell (Kitchen fast TTS, "loft + fast kitchen", audio off, the "Kitchen
speaker playing" trigger) and Baby Monitor (speaker alert to Loft, kitchen
TTS). The voice orchestrator is fine (client 1.4.3 = schema 33). First HA
with a schema-28 client: **2025.12.0** (client 1.3.2). HA runs the floating
`:stable` tag, so a plain pull jumps to 2026.10.2 (11 months): pin a version
on purpose. Order: HA upgrade (its own plan + checks), then MA 2.10.7.

**Interim patch (live 2026-10-20 16:47).** `/home/pi/music-assistant`:
- image pinned to the running 2.6.3 digest
  `ghcr.io/music-assistant/server@sha256:0986d48f962c948d9023e7e1d5c8069ce4e312d90aa31fdfa6fb7656e969b132`
  (was `:latest`);
- `./patches/snapcast/__init__.py` bind-mounted read-only over the provider.
  Diff in `ma-upgrade-check/patches/snapcast-2.6.3-stop-order.patch`:
  `cmd_stop` cancels the ffmpeg feeder first, waits up to 2 s for the stream
  to go idle, then switches the group to default and removes the stream;
  the two unbounded `while stream.status != "idle"` loops (end of music, end
  of announcement = the announcement-lock wedge) are capped at 15 s.
- Backups: `docker-compose.yml.bak-pre-snapcast-patch-20261020`,
  `data-backup-20261020.tgz` (55 MB).
- Rollback: `cp docker-compose.yml.bak-pre-snapcast-patch-20261020
  docker-compose.yml && docker compose up -d --pull never`.
- Verified: `ma_check.py smoke --audio` 17/17 three times in a row, SEGV
  count unchanged (6); snapserver log now shows `playing => idle` BEFORE
  `RemoveStream`. Before the patch the same play+stop crashed it on the
  first try.
- `/home/pi/music-assistant` is not a git repo: the patch file and compose
  there are backed up only by this diff + the notes here.

**New compatibility notes for 2.10.7 (add to the test pathway):**
- Stream names change: music = `Music Assistant - <queue id>` (not display
  name), announcements = `Music Assistant - <md5[:6]> (announcement)`.
  home-audio-adapter `_remove_stale_snapcast_stream` is name-agnostic (takes
  the id, only removes idle streams with no connected clients): fine.
  `ma-announce-watchdog` README describes the old name; check its code keys
  on `announcement_in_progress`, not names.
- Idle music sources now stay on the snapserver between plays (one per
  queue); expect them in `Server.GetStatus`.
- Still no PAUSE feature on snapcast players (features: PLAY_MEDIA,
  PLAY_ANNOUNCEMENT, SET_MEMBERS, VOLUME_SET, VOLUME_MUTE): zone-music F4
  (pause = stop + resume_pos) is unchanged.
- External snapserver still supported (`CONF_USE_EXTERNAL_SERVER`).
- The provider was split into several modules (player.py, provider.py,
  ma_stream.py, socket_server.py) - a rewrite, so retest grouping/ungrouping
  (Tempo presets, party mode) and announcements over music, not just play.

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
