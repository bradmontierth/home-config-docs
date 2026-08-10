# Music to a zone — scope

**Asked for:** 2026-08-09. Music on the master bath speakers while the kids are
in the tub, started by voice from the master closet satellite.
**Status:** scoped, nothing built.

**Done looks like:** "okay computer, play Charlie Hope" said into the closet
mic starts music on the bath speakers at a bath volume, "stop the music" stops
it, and nothing about it touches the kitchen — not the queue, not the screen,
not the NFC jukebox's idea of what card is playing.

## The shape of it

`music.py` is not kitchen-specific in any interesting way. Its resolver — the
ASR-robust library index that survives "rafi" and "deo" — is already pure: it
turns a spoken phrase into a URI. What is kitchen-specific is a single constant
threaded through every entry point:

```python
qid = config.MA_QUEUE_ID          # "e4:5f:01:67:1e:56", the kitchen squeezelite
```

`play()`, `control()`, `now_playing()`, `duck()` and `unduck()` each reach for
it directly. The change is to make the queue an argument resolved from the
satellite that heard the command, exactly the way the timer alarm resolves its
room from `satellite_zones.json`. Everything else follows from that.

Table entry, alongside the alarm columns that already exist:

```json
"master": {
  "ma_player": "ma_shower",  "snap_client": "shower",
  "music_player": "ma_shower", "music_volume": 30, "music_max_volume": 40
}
```

`kitchen` gets `"music_player": "e4:5f:01:67:1e:56"`, which retires the
`"music": true` flag and `zones.owns_music()` with it — "is this the music
room" stops being a question once every room can have its own queue. The new
question is "which queue does this room duck", and a room with no
`music_player` ducks nothing.

## Feasible? Yes, and it is already in use

`ma_shower` is a snapcast player and MA's snapcast provider implements
`play_media`. Not theory — the neighbouring zones hold real queues right now:

```
ma_loft            30 items   Charlie Hope — Hello Song   (builtin://track/… Navidrome)
ma_simon_room      29 items
ma_master_bedroom  51 items
ma_shower           0 items
```

Kids' music, to zone players, through MA, today. The bath is the one zone that
has never been asked.

## What the plumbing actually does

Worth reading before writing code — four of these are counter-intuitive and two
of them are live bugs waiting for the first bath play.

**One stream, five groups, and a silence generator.** The external snapserver
(`192.168.10.140:1705`, v0.35.0) has exactly one stream, `default`, which is
`process:///usr/local/bin/snapcast-silence?…48000:16:2`. Each zone client sits
in its own group pointed at it. On play, MA creates `Music Assistant - Shower`,
points that group at it, and on `cmd_stop` reverts the group to `default` and
deletes the stream. So zones are genuinely independent — bath music cannot leak
into the loft — but the group's stream pointer is shared mutable state between
MA and everything else that touches snapserver.

**A snapcast zone cannot pause.** Its `supported_features` are
`volume_mute, set_members, volume_set, play_announcement` — no `pause`, no
`enqueue`. `player_queues.pause()` sees that and sends `cmd_stop` instead
(`controllers/player_queues.py:646`), having first saved `resume_pos`. So
"pause the music" in the bath is stop-and-remember: resume works and picks up
the position, but it tears down and rebuilds the snapcast stream, and if the
pause was long the amp has gone back to sleep in the meantime. Functional, but
it is not a pause and should not be described as one in the build notes.

**An announcement does not stop the music — it talks over it.** The provider
switches the group to an announcement stream, plays, and switches back to the
music stream if the player is not idle. The queue is never paused, so the song
keeps streaming underneath and you lose exactly that stretch of it. A bath
timer ring is one spoken line plus N chunk announcements, so a ring over music
silently eats ~30s of the song and drops you back mid-verse.

**MA restores the announcement volume from its own cache, and its cache is
wrong.** The provider saves `player.volume_level` before an announcement and
writes it back afterwards. Right now MA reports `ma_shower` at **0** while
snapserver reports it at **20**. That divergence is the ratchet already
documented in `ma-volume-source-of-truth`, and with music playing it gets a new
way to hurt: a ring at alarm volume 45 over music at 30 can hand the room back
at 45, or at 0, depending on what MA believed at the time.

**So ducking, as written, is a silent no-op on a zone.** `music.duck()` reads
`player.volume_level` (0), computes `target = max(MUSIC_DUCK_MIN, 0) = 5`, sees
`5 >= 0` and returns having done nothing. The same stale read makes
`control("volume_up")` set the bath to 10 — "turn it up" would make it quieter.
Volume reads on a snapcast zone have to come from snapserver;
`zone_alarm._snap_volume()` already does this and wants hoisting into a shared
helper.

**Duck state is one global.** `_duck = {"count": 0, "restore": None}` plus one
watchdog task, for the whole house. Two rooms playing means the kitchen's
unduck restores the bath's saved volume onto the kitchen. Per-queue state, or
the second room breaks the first.

**MA 2.6.3's announcement wait is the wedge bug.** `while stream.status !=
"idle": await asyncio.sleep(0.25)` — unbounded, no connection check, and a
snapserver drop mid-announcement pins `announcement_in_progress` true forever.
See `music-assistant-upgrade-plan.md`; the watchdog is the interim net. Music
in the bath does not add announcement traffic by itself, but it does add
another reason for that player to be busy when one arrives.

**home-audio-adapter has already paid for two of these lessons.** It plays
podcast queues to these same zones and had to learn that (a) a stale
`Music Assistant - <name>` stream blocks the next play and must be removed and
retried, and (b) `shuffle` / `repeat` / `dont_stop_the_music` persist on a
queue across sessions and must be cleared explicitly. Our `play()` sets shuffle
every time; it does not touch the other two. Borrow both, don't rediscover them.

## Phase 1 — the useful half

Result: voice music in the bath, right volume, stops when told, kitchen
untouched.

| File | Work |
|---|---|
| `music.py` | Queue id becomes a parameter on `play`/`control`/`now_playing`/`duck`/`unduck`. `_duck` becomes per-queue. Volume reads route through snapserver for snapcast players. Clear `repeat` and `dont_stop_the_music` alongside the existing shuffle set. |
| `zones.py` | `music_queue_for(sat)` and `music_volume_for(sat)`; delete `owns_music()`. |
| `app.py` | `play_music` / `music_control` / `music_query` pass the turn's sat. `/music/duck` and `/music/unduck` resolve a queue instead of asking permission. `show_music` gated on `events.on_dashboard()` — same fix the timers just got. `_notify_jukebox_takeover()` only for the kitchen queue. |
| `zone_alarm.py` | Hoist `_snap_volume()` into the shared helper `music.py` will use. |
| `satellite_zones.json` | The three music columns. **Both copies** — the repo file only seeds, the live table is `/data/satellite_zones.json` in the container. |
| new | Amp wake before the first note: `broadcast.amp_wake(rooms, music_volume)` then the same ~1s gate the ring uses, or the opening bars go into a sleeping amp. |

Tests: the duck no-op on a stale zone volume, per-queue duck isolation, the
kitchen-only side effects, and the room→queue resolution. All unit-level; the
existing `test_zone_alarm.py` patterns cover the shape.

## Phase 2 — the interactions, measured in the room

- **A bath timer ringing over bath music.** Three candidate behaviours: duck
  the room's music for the whole ring (recommended — the ring already ducks,
  it just needs to duck the right queue), stop the music outright, or leave it.
  Whichever, the volume MA hands back has to be ours, not MA's cached guess.
- **Stale-stream repair on play failure**, and a verify-after-stop that the
  group is back on `default`. Without it, an MA crash mid-play can leave the
  shower group pointed at a deleted stream — which would take the bath *timer
  announcements* down with it, i.e. break the thing shipped yesterday.
- **An auto-stop cap.** Kids in the tub cannot reach the closet mic. Something
  has to end it if nobody says so.
- **A "bath music off" button** on the existing Voice Buttons Node-RED tab,
  which is a table edit rather than code.

## Decisions I need from you

1. **Volume.** Reply is 20, alarm is 45. Music over running water and two kids
   is probably 28–35, and it wants a hard cap so "turn it up" can't walk it
   into alarm territory with them in the room. One measurement session settles
   both, the same way the reply volume got settled.
2. **Ring over music** — duck, or stop the music? Ducking keeps the bath
   feeling like one room; stopping is unambiguous.
3. **How it ends when nobody says so** — is a 60-minute cap right, or does the
   bath's presence sensor get a vote? (`static_presence` sticks ON for days on
   that EPP, so I would not make it load-bearing.)
4. **Phase 3, or not:** "play Charlie Hope in the bath" said from the kitchen.
   That needs a room slot in the intent schema and a resolver shared with
   `broadcast_rooms.json` — real work, and only worth it if you'd actually
   start bath music from downstairs.

## Not doing

- **Routing through home-audio-adapter.** It models AntennaPod sessions —
  materialised episode queues with resume positions — not a library player.
  Its isolate bridge and its two hard-won lessons are worth borrowing; its
  session model is not.
- **Grouping the bath with the kitchen.** Different queues in different rooms is
  the whole point; sync groups are what `isolate` exists to undo.
- **Anything about wake-over-music.** The kitchen's problem (item 4) is a mic
  sitting beside the big speakers. Here the mic is in the closet and the music
  is in the bath, through a doorway — the geometry is favourable, and it should
  be tested rather than assumed to be a problem.
