# Zone music: "play Raffi" from the master closet

**Status:** scoped 2026-08-09, nothing built.
**Ask (Brad):** play music into the master bath while the kids are bathing.
**Scope as written:** music follows the satellite that heard the command. The
bath is the first customer; the loft and the kids' rooms come free.

The library resolver does not change at all. What changes is that music.py
currently believes there is exactly one place music can play, and there isn't.

---

## 1. What exists today

| Piece | Where | State |
|---|---|---|
| ASR-robust library resolver | `orchestrator/music.py` `_resolve_library` | room-agnostic already — it returns a URI, it doesn't care who plays it |
| `play_music` / `music_control` / `music_query` intents | `orchestrator/app.py` | all hardwired to `config.MA_QUEUE_ID` (the kitchen squeezelite box) |
| Per-satellite routing table | `orchestrator/satellite_zones.json` | has `ma_player`, `snap_client`, `volume`, `alarm_volume` for `master` |
| Amp pre-wake | `broadcast.amp_wake(rooms, volume)` | used by replies and by the zone ring |
| Authoritative volume read | `zone_alarm._snap_volume()` | reads the snapserver, because MA's cached value lies |
| Zone ring | `orchestrator/zone_alarm.py` | shipped 2026-08-08, rings the bath |

So the resolver, the room table, the amp wake and the volume read are all
already built and proven. This is mostly a plumbing job.

---

## 2. How audio actually reaches the bath (measured 2026-08-09)

Worth writing down, because two of the findings below are only explicable
from the topology.

- **One snapserver**, external, on `192.168.10.140:1705` (snapserver 0.35.0).
  All five zone snapclients run *on that Pi* (`::ffff:127.0.0.1`) and feed the
  MA1240a through the DAC HAT. MA at `.217` drives it over RPC.
- **One permanent stream, `default`** — and it is a silence generator
  (`process:///usr/local/bin/snapcast-silence`, `48000:16:2`, flac, 40ms
  chunks). It exists to keep the clients connected, not to carry audio.
- **Each zone is its own group**, all parked on `default` when idle.
- On playback MA **creates a stream per player** (`Music Assistant - Shower`),
  points that one group at it, and on stop deletes it and puts the group back
  on `default` (verified in the provider source, `cmd_stop`).
- An **announcement gets a second stream** (`Music Assistant - Shower
  (announcement)`), which always takes the group; when it finishes the group
  goes back to the music stream if the player is still playing, or to `default`
  if it isn't. The queue is **never paused** for it — the song keeps streaming
  underneath and you simply don't hear that stretch of it.
- The kitchen is *not* on this amp — it's a squeezelite player on the mini PC.
  **Kitchen music and bath music are fully independent.** Nothing to arbitrate.

Zone playback is not theoretical here: `ma_loft` (30 items), `ma_simon_room`
(29) and `ma_master_bedroom` (51) all hold Charlie Hope tracks from Navidrome.
The zones have played kids' music before, just never from voice.

---

## 3. What breaks if you just point `play()` at `ma_shower`

Nine findings, in the order they would bite. The first three are silent
failures, which is why this is a plan and not a one-line change.

(Scoped twice on 2026-08-09 by two sessions working in parallel, which is why
this doc has one author's structure and both authors' findings. Where they
disagreed the code was re-read; nothing here is unreconciled.)

**F1 — Ducking silently does nothing on a zone player.** `music.duck()` reads
`player.volume_level` from MA's cache. Right now MA reports `ma_shower` at
**0** while the snapserver reports **20** — the divergence recorded in
`ma-volume-source-of-truth` is live as I write this. duck() computes
`target = max(5, 0) = 5`, sees `5 >= 0`, and returns having done nothing.
Every volume read on a snapcast zone has to come from the snapserver.
`zone_alarm._snap_volume()` already does exactly this; hoist it and share it.

**F2 — "Turn it up" turns it down.** Same stale read: `control("volume_up")`
does `0 + 10` and writes 10 to a zone that was playing at 20.

**F3 — Duck state is a single global.** `_duck = {"count", "restore"}` plus one
watchdog task, process-wide. Two rooms playing means the kitchen's unduck
restores the bath's saved volume onto the kitchen. Has to become per-queue.
This also retires `zones.owns_music()` (added yesterday for the ducking bug):
"the one music room" stops being a true idea. It becomes
`zones.music_queue_for(sat)` → the queue this room ducks, or None.

**F4 — There is no pause on a snapcast zone.** The provider advertises only
`volume_set`, `volume_mute`, `set_members`, `play_announcement`. MA's queue
controller falls back to `cmd_stop` — but it saves `resume_pos` first, so
resume picks up where it left off. Functionally fine; two consequences worth
knowing: a "pause" tears the snapcast stream down, and a *long* pause lets the
amp fall asleep, so the resume needs the same wake treatment as a fresh start.

**F5 — The first seconds get swallowed by a cold amp.** Same failure that ate
two timer confirmations on 2026-08-08. `amp_wake(rooms, volume)` then a short
gate before `play_media`, exactly as the ring does now.

**F6 — A timer ringing over bath music will strobe.** This is the one I'd want
measured before promising anything. Snapcast volume is **per client, not per
stream**, so MA's announcement path saves the client volume, sets it to the
announcement volume, switches the group to an announcement stream, plays,
restores the volume, and switches the group *back to the music stream*. Our
ring is 1 speech announcement + 5 chunk announcements (`CYCLES=14`,
`CYCLES_PER_CHUNK=3`). So a bath timer over bath music = six of those
switches, and in the ~2s between chunks the group flips back and you hear
music at resting volume, then beeps at 45, then music, then beeps. Two things
sharpen it further. The queue is never paused underneath, so the song is still
advancing through all of that and comes back roughly half a minute along. And
the volume the provider restores after each announcement is
`player.volume_level` **read from MA's own cache** — the number F1 says is
wrong — so a ring over music can hand the room back at 0 (silent music, no
error anywhere) or leave it at 45. Options: duck the room's music for the whole
ring (my recommendation), or stop it outright at ring start, or leave it and
see. Cheap to test once F1–F3 are in, and whichever wins, the volume that ends
up on the client afterwards has to be one we wrote, not one MA remembered.

**F7 — A wedged stream would break the timer alarm we just shipped.** A clean
stop reverts the group to `default`. An MA crash mid-play does not — the group
is left pointing at a stream that no longer exists, and then *announcements to
that zone go silent too*. `home-audio-adapter` already solved this
(`_remove_stale_snapcast_stream`: if the stale stream is idle with no
connected clients, remove it and retry the play once). Borrow it verbatim, and
verify the group is back on `default` after a stop.

**F8 — Kitchen-only side effects fire for a bath play.** `events.emit("show_music")`
pops the jukebox now-playing modal on the kitchen kiosk, and
`_notify_jukebox_takeover()` clears the NFC card marker. Neither should happen
for music two floors up. Same bug class as yesterday's timer-display leak, and
the same fix pattern (`events.on_dashboard` / queue check).

**F9 — Every announcement to that zone is a roll of the 2.6.3 dice.** The
provider ends `play_announcement` with `while stream.status != "idle": await
asyncio.sleep(0.25)` — unbounded, no connection check — so a snapserver drop
mid-announcement pins `announcement_in_progress` true and silently swallows
every later announcement to that player until MA restarts. That is the bug
`music-assistant-upgrade-plan.md` exists for, and the announce watchdog is the
interim net. Music does not create announcements, but it does make the shower
player busy far more often, and a busy player is where this bites.

One thing that is *not* a problem: **wake-over-music is easier here than in the
kitchen.** The mic is in the closet, the speakers are in the bath, and there is
a door between them. The kitchen's open item 4 (mic beside the big speakers)
does not transfer. The closet Pi still has no `stop.onnx`, so a ring during
music is dismissed by the ASR path only.

---

## 4. Phase 1 — the room plays music

Everything needed for "okay computer, play Raffi" in the closet to play in the
bath at a sane volume, and "stop the music" to stop it.

1. **Table columns** (`satellite_zones.json`, and the live `/data` copy — the
   repo file only *seeds*, it does not update an existing table):
   ```json
   "master": { "music_player": "ma_shower", "music_volume": 30, "music_max_volume": 40 }
   "kitchen": { "music_player": "e4:5f:01:67:1e:56" }
   ```
   `music_max_volume` is a real requirement, not polish: it stops "turn it up"
   from walking a speaker toward alarm volume with kids under it.
2. **`zones.music_queue_for(sat)`** replacing `owns_music()`; NULL/unknown sat
   still resolves to the kitchen, which is the pre-`sat` behaviour.
3. **Thread the queue through `music.py`** — `play`, `control`, `now_playing`,
   `duck`, `unduck`, all defaulting to `MA_QUEUE_ID`. Per-queue duck state.
4. **Snapserver-authoritative volume** for any queue with a `snap_client`.
5. **Amp wake + gate** before `play_media` on an amp zone.
6. **Explicit queue hygiene** — shuffle we already set; also set repeat and
   `dont_stop_the_music` off. These persist per queue across sessions and the
   adapter got burned by exactly that.
7. **Kitchen-scope the kiosk pop and the jukebox notify.**
8. Tests: the existing `MusicDuckScopeTest` gets rewritten around the new
   helper; new coverage for the stale-volume read and per-queue duck isolation.

Net: a few hundred lines, mostly in `music.py`, plus tests. No new services.

## 5. Phase 2 — living with it

- **F6 measured**, and whichever of the three behaviours wins, implemented.
- **F7**: stale-stream repair + post-stop group verification.
- **An auto-stop cap** (60 min?). The kids can't reach the closet mic, so
  something has to end it if everyone walks away. A plain timer beats keying
  off the bath presence sensor — `static_presence` there sticks ON for days.
- **A physical off switch**: one more MQTT button on the existing Node-RED
  "Voice Buttons" tab is nearly free and works when nobody wants to talk.

## 6. Phase 3 — optional

"Play Raffi **in the bath**" said from the kitchen. Needs a room slot in the
intent schema and a shared room resolver — `broadcast_rooms.json` already
holds that mapping, so it's a merge rather than a new concept. Not needed for
the stated ask.

---

## 7. Decisions I need from you

1. **Volume.** Reply is 20, alarm is 45. Music over running water and kids —
   I'd start at 30 with a cap of 40, but that wants the same one-off
   measurement session the reply volume got.
2. **F6:** duck the music under the ring, or stop it outright?
3. **Auto-stop:** 60 minutes, or don't?
4. Is the loft / kids' rooms in scope now, or bath-only? It is one table row
   each once Phase 1 lands, but each new room is another thing that can be
   ringing, ducking and talking at the same time.

## 8. What I would not do

- **Don't route this through `home-audio-adapter`.** It plays a materialized
  queue of episode URLs with resume positions for AntennaPod. It's the wrong
  shape for a library player, and we'd be maintaining the resolver's output in
  two places. Borrow its snapcast lessons, not its API.
- **Don't group the bath with the kitchen.** Different provider, different
  amp, and the isolate bridge exists precisely to keep zones apart.
