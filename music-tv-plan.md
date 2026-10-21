# Music TV Plan (Navidrome on the TV + the TV as a house speaker)

Our own Android TV music app for the Shield, in the same family as Photos TV
(`photos-tv-plan.md`) and Media Center (`media-center-guide.md`). Three jobs,
easiest first:

1. TV already on, remote in hand: browse Navidrome, play a playlist, show
   album art.
2. Cast from the phone: pick "Family Room TV" in our Tempo fork; the Shield
   wakes, CEC turns the TV on, the TV's optical out turns the soundbar on,
   music plays.
3. Party mode: the TV plays in sync with the kitchen big speakers.

Later: voice "play X on the TV", and "show me Simon on the TV" (cameras).

## Decisions (2026-10-19)

- **Browse Navidrome directly** (Subsonic API: playlists, albums, artists,
  search, server-resized cover art). Music Assistant's library layer is where
  our worst bugs lived (`ma-playlist-track-cache`); it is not used for browsing.
- **House playback goes through `home-audio-adapter` (:8461), not MA
  directly.** The TV app is a third client of the bridge next to Tempo and
  inherits its MA workarounds (stale snapcast stream repair, replay-after-end
  reload, amp lease, shared sessions).
- **Local mode for job 1:** Media3 ExoPlayer straight from Navidrome on the
  Shield. Instant pause/seek, works when MA is down. "Also play in the kitchen"
  hands the queue + position to the bridge (same as Tempo's local→remote
  handoff).
- **The TV's speaker identity is a Snapcast client** on the Shield, so MA sees
  it as a snapcast player and the bridge lists it automatically
  (`LISTED_PLAYER_PROVIDERS = {"snapcast", "squeezelite"}`).
- **The kitchen jukebox stays on squeezelite.** Squeezelite (slimproto) and
  Snapcast are separate clock domains; MA's cross-provider groups are not
  sample-synced. Party mode uses a second, party-only snapclient on the
  kitchen mini PC writing to the same `kitchenmix` dmix.
- **Separate app** (working name "Music", `com.local.musictv`), not a Media
  Center section: it needs an always-on background service and updates
  independently. Same toolchain as Photos TV (Kotlin, Compose for TV, Media3,
  Coil, JDK 17, memory-capped Gradle).
- Reuse upstream snapclient (the native lib Snapdroid bundles), do not write
  our own Snapcast protocol/sync code.

## Facts verified 2026-10-19

- Shield 192.168.40.67 (IoT VLAN 40) reaches the snapserver
  192.168.10.140 on 1704 (audio), 1705 (control), 1780 (http).
- Shield audio route: HDMI → Samsung UN70TU700 (`media_player.family_room_tv`,
  samsungtv integration; LCD, so no burn-in concern) → optical → soundbar.
- HA sees the Shield as a Cast device (`media_player.shield_2`, cast
  platform). MA has no Chromecast provider enabled.
- MA 2.6.3. Players: 5 snapcast zones (`ma_*`) + squeezelite
  `e4:5f:01:67:1e:56` (kitchen).
- Snapserver v0.35.0 on home-audio-pi.
- The bridge only lists players MA reports `available`. An asleep Shield puts
  app uids on REJECT_ALL (Photos TV gotcha), so a Shield snapclient drops off
  while the TV is off → the bridge needs an always-listed TV target with a
  wake hook.
- Kitchen AEC: anything written to `kitchenmix` is in the XVF3800 reference
  (`kitchen-aec-guide.md`), so a kitchen snapclient is cancellable with no
  satellite change. The soundbar is NOT in that reference.

## Phase 0: sync spike (no app code)

Installed 2026-10-19:

- **Kitchen mini PC** (`ssh big-speaker-mini-pc`): upstream snapclient
  0.35.0 (`snapclient_0.35.0-1_amd64_bookworm.deb`, sha256 b71575bc…, only
  extra dep libavahi-client3). The package's `snapclient.service` is
  **masked**. Our unit `/etc/systemd/system/snapclient-party.service`
  (User=pi, `--host 192.168.10.140 --hostID kitchen-party --player alsa
  --soundcard kitchenmix`) is **disabled and stopped**; start it only for
  the test. Rollback: `sudo systemctl stop snapclient-party; sudo apt remove
  snapclient; sudo systemctl unmask snapclient; sudo rm
  /etc/systemd/system/snapclient-party.service`.
- **Shield**: Snapcast app `de.badaix.snapcast` v0.29.0.2 (upstream
  Snapdroid, last release 2024-12-02, sha256 cd835c12…, has armeabi-v7a +
  leanback launcher). Rollback: `adb uninstall de.badaix.snapcast`.
  - Configured 2026-10-19 evening: the app found 192.168.10.140 by mDNS on
    its own (discovery DOES cross to VLAN 40). The top-bar Play button
    (`action_play_stop`) starts the local client: snapserver client id
    `624d338e-f13c-444b-8f45-765b085fb035`, name "SHIELD Android TV", ip
    192.168.40.67, client 0.29.0, latency 0, volume 100 (right for a TV: the
    soundbar is the real volume), parked on `default`. MA player
    `ma_624d338ef13c444b8f45765b085fb035`; listed by the bridge
    `/v1/devices`, so it is selectable in Tempo now. Runs as a foreground
    service (`SnapclientService`) after leaving the app.
  - Not yet checked: whether it reconnects after the Shield sleeps/wakes and
    after a reboot (the app has RECEIVE_BOOT_COMPLETED), and whether to rename
    it "Family Room TV" (snapserver `Client.SetName`).

Test steps and results:

1. DONE 2026-10-19: Shield client connects (0.29 client against 0.35
   server works), appears in MA and in the bridge's `/v1/devices`.
2. DONE 2026-10-19: Brad played to the Shield alone from Tempo; works, no
   noticeable start delay. Pause behaviour not judged yet.
3. DONE 2026-10-19: `snapclient-party` started, MA `players/cmd/group_many`
   (leader = Shield, child `ma_kitchenparty`), 1 Hz click track
   (`http://192.168.10.217:8811/clicks15.flac`, a temporary server) played
   as a URL on the Shield queue. A mute cycle of 10 s both / 14 s TV-only /
   6 s kitchen-only / 10 s both tells the speakers apart in a phone
   recording. Use **volume 0, not mute**: a muted snapclient gets no chunks,
   closes ALSA after 5 s and re-syncs on unmute.
   - Phone at 4.04 m from the soundbar and 2.26 m from the right big
     speaker (sound needs 11.8 ms and 6.6 ms to arrive). Measured kitchen
     click 11.2 ms after the TV at the phone, i.e. **the kitchen leaves its
     speakers 16.4 ms after the TV** with both latencies at 0. The TV chain
     (Shield → HDMI → TV → optical → soundbar) is the FAST one. The kitchen
     path goes through the XVF3800 USB playback since the AEC cutover (the
     AEC notes record ~30 ms extra speaker latency there).
   - At that spot the TV clicks were ~18 dB louder than the kitchen at 35 %.
4. DONE: `Client.SetLatency kitchen-party 16` (persisted in
   home-audio-pi `/home/pi/server.json`; the snapserver data dir is
   `$HOME` of user pi, NOT /var/lib/snapserver). Re-measured: kitchen
   arrives 5.9 ms before the TV at the phone vs 5.2 ms expected for zero
   emission offset → **residual ~0.7 ms**. The 2nd recording was muffled
   (low-frequency rumble, clicks ~40x the floor only after per-second
   alignment) but still conclusive.
5. OPEN: stability after a Shield reboot, a TV power cycle and an hour of
   play. Also whether the offset changes with the TV's picture mode or
   soundbar mode.
6. OPEN: kitchen wake-word behaviour with both playing.
7. Teardown after each test: stop the MA queue, ungroup, `systemctl stop
   snapclient-party`, stop the temporary server. Tools in
   `/home/pi/tmp/synctest/` (gen_clicks.py, run_test.py, snap.py, ma.py,
   analyze.py, inspect3.py, server.py).

Expected TV + soundbar delay was "50-200 ms" (a guess). In fact the TV is
~16 ms AHEAD of the kitchen.

### Snapserver crash found during the test (pre-existing)

snapserver 0.35.0 on home-audio-pi SEGFAULTs inside `Stream.RemoveStream`
of a stream that is still playing, which is what MA does on every
stop/replace. 5 SIGSEGVs in 30 days, 3 on 2026-10-19: 07:57:23 (Simon
Room, before any of this work), 21:20:59 (Shield, Tempo test stop), 21:37:51
(Shield, click track replace). systemd restarts it in ~2 s and every client
reconnects, but every zone drops for those seconds and setting changes
younger than about a second can be lost. The Shield does not cause it, but
each Shield stop is another chance to hit it. Needs its own fix (MA stop
path or a different snapserver build) before party mode is relied on.

## Phase 1 built (2026-10-19/20)

Repo `/home/pi/music-tv`, pushed to `github-illuminate:bradmontierth/music-tv.git` (`main`).

- **Gateway** `server/` = docker project `music-tv`, port 8808 on the Beelink.
  Navidrome has ONE account (`admin`) that owns all 20 playlists (17
  private), so a low-privilege TV user is not possible. Secrets in
  `/home/pi/cecret_lake/music_tv/` (`navidrome_user`, `navidrome_password`
  copied from the Spotiflac `.env`, `device_tokens` with `shield <token>`).
  Whitelisted read views + scrobble; anything else = 404; no token = 401.
  `stream` forces `format=raw`, Range passes through (206). Warm stream
  start 7-18 ms; the very first read after idle took 2.9 s (disk).
- **TV app** `tv/` = `com.local.musictv` "Music", 2.1 MB release APK,
  installed on the Shield over adb and provisioned with
  `am start -n com.local.musictv/.MainActivity --es mt_url http://192.168.10.217:8808 --es mt_token <token>`.
  Shield reaches :8808 (checked with adb-shell nc).
- Verified on the Shield with screenshots: playlist grid, playlist page,
  Back restores focus, Play starts in ~2 s, Now Playing, hold OK on a song
  = repeat this song (status "Repeating this song").
- Fixed after review (commit 2): clipped "60 hr 35" (>= 10 h shows hours
  only); repeat/shuffle overflowed the controls row (labels now "Repeat" /
  "Repeat song" / "Repeat all" / "Shuffle", amber when on); search song rows
  show a 40 dp cover. All re-screenshotted on the Shield 2026-10-20 07:15.
- Verified 2026-10-20: repeat-one really loops (Five Little Pumpkins, 0:30:
  position 26.9 s -> 1.5 s, same song, still playing); Next while on repeat
  moves on and keeps repeat (normal); Albums loads all 1,399 (Recently added
  / A-Z); Search type-ahead + "Nothing matches" message.
- 2026-10-20 scroll fix (commit 40d53b4): CalmPivot now uses Photos TV's
  critically damped spring (damping 1, stiffness 1800) and 0.42 pivot for
  grids instead of a 220 ms tween (which re-started its easing on every
  bring-into-view re-aim and crept in with a long tail). Shield, 20
  album-row moves: ~14 frames (~230 ms) per move, p50 10 ms. gfxinfo
  "janky" 16 % is mostly adb-injection input latency; real cost is ~1 slow
  UI frame per new row (laying out 6 covers + titles).
- Not verified in this app: the mic (voice search). Same code as Photos TV,
  where it works on this Shield.
- Remote-driving notes: Back on the Playlists root exits the app; Left from
  the grid lands on the menu item level with the focused row (often Search);
  the Search field opens the keyboard on focus, so OK there types a letter.
- Gotcha: Google Assistant (katniss) mutes STREAM_MUSIC when the Shield
  sleeps and unmutes on wake, so "Muted: true" while asleep is normal.
  A KEYCODE_VOLUME_MUTE sent while asleep goes to `shieldtech.hooks` as a
  toggle; don't send blind mute toggles. Shield log times are LOCAL time.

## Phases

1. **Local client** (job 1). Home (recent, playlists), Playlists, Albums,
   Artists, Search (voice + keyboard), big now-playing screen with art,
   queue. Dedicated non-admin Navidrome user for the TV, credentials in
   `/home/pi/cecret_lake/music_tv/`, provisioned over adb like Media
   Center's `mc_token`. Scrobble to Navidrome.
2. **Receiver** (job 2). App embeds snapclient as a foreground service
   started at boot. Bridge: always list "Family Room TV"; on play, a Beelink
   wake hook runs `adb shell input keyevent KEYCODE_WAKEUP` (CEC turns TV +
   soundbar on) + `am start` our now-playing screen, waits for the client in
   snapserver `Server.GetStatus`, then a short gate for soundbar warm-up
   (same lesson as the cold amp, zone-music F5). If another app is in the
   foreground (Netflix), the phone asks before taking the TV. The app shows
   now-playing from the bridge session for its target.
3. **Party mode** (job 3). Bridge route preset "Kitchen + TV" (and maybe
   "Everywhere + TV") using `kitchen-party` + the Shield client; the bridge
   stops the squeezelite kitchen queue when the preset starts. Latency
   offset from phase 0.
4. **Voice.** "Play X" stays on the kitchen squeezelite; "on the TV" targets
   the TV through the same wake hook. Ducking: the familyroom zone currently
   ducks the kitchen queue (`familyroom-music-duck`); it must duck whichever
   one is playing.
5. **Cameras.** "Show me Simon on the TV" opens a full-screen camera view
   from Lookout (lookout.lan:8805, go2rtc). The Shield decodes HEVC in
   hardware, which may make the Jetson transcode unnecessary. The kitchen
   display stays the default target.

## Party mode built (2026-10-20)

In the Tempo bridge (`home-audio-adapter` cb4ace3), so it appears in Tempo
with no app change:
- Groups **Kitchen + TV** (Shield leads, so the kitchen can drop out),
  **Whole House** (5 upstairs zones + kitchen), **Whole House + TV**.
  Listed only while every member is available.
- Kitchen = `snapclient-party.service` on the kitchen mini PC, now ENABLED
  at boot, snapserver client `kitchen-party` named "Kitchen Party"
  (latency 16 ms, volume 35 %), hidden as a single room in Tempo.
- Per-member volumes: TV 100 (soundbar sets the level), kitchen party
  `KITCHEN_PARTY_VOLUME` (35, needs a listening check against the jukebox's
  usual 65 on squeezelite); upstairs zones 30 like "All Speakers".
- Party routes stop the kitchen squeezelite player first (shared kitchenmix
  dmix). A 2 s watch in the bridge takes the kitchen out of the group when
  its own player starts (jukebox card, voice); the other rooms keep playing.
- TV routes wake the Shield over adb from the bridge container (Beelink's
  trusted key mounted read-only) and wait 6 s (`TV_WARMUP_S`) for the
  soundbar: Kitchen + TV from a sleeping Shield took 16.7 s to start;
  Whole House (no TV) 2.9 s.
- Live check: `home_config/ma-upgrade-check/party_check.py [--target ...]
  [--detach]`, all passing on the revised MA patch.
- Groups persist after a session stops (existing bridge behaviour): a later
  solo route or the Node-RED isolate call ungroups.

Open: kitchen party volume calibration by ear; the voice assistant does not
duck party music (it ducks the squeezelite queue only) -> next; "TV is in
use, play anyway?" prompt; TV latency stability re-check.

## TV shows the house's music (2026-10-20)

Music app (music-tv 0dd116d, installed on the Shield): `House.kt` polls the
bridge's `/v1/sessions` every 2 s while the app is open and follows the
newest playing/paused session on a TV route (the TV alone, Kitchen + TV,
Whole House + TV). Now Playing then shows it ("Playing on Kitchen + TV",
art via the gateway's `getSong` on `episode_id` = Navidrome song id,
progress from `audible_position_ms` + elapsed, up next from the session
queue); play/pause/next and the remote's media keys go to the bridge
(`/v1/pause|resume|next`, previous = `/v1/seek 0`), so the whole group
follows. Verified on screen with Brad's live Kitchen + TV session (paused).
Bridge ae0b6a2 + b650e08, DEPLOYED 2026-10-20 18:1x: TV routes launch the
app with `--ez mt_house true` after waking the Shield, or if it is awake on
the Shield's HOME app (resolved on the device: `com.local.tvhome`) /
Google launcher / screensaver / Snapcast / Photos / Music; never over
another app. adb is warmed at bridge startup (a cold adb server added ~13 s
to the first TV route). Verified with `party_check.py --tv-ui` (9/9, twice
in a row and from the home screen): Kitchen + TV starts in ~3.5 s with the
TV awake, Music comes up by itself, the remote's play/pause pauses the
group through the bridge.
Found + fixed on the way: `_queue_is_playing_media` treated a STOPPED MA
queue that still held the same first item as playing, so replaying the same
song on the same target after a stop did nothing (would also hit Tempo).

Security finding (FIXED in the open list, b650e08): `/v1/sessions` returned
Tempo's queue `media_url`s with the Navidrome admin password (`p=enc:<hex>`,
reversible) to any LAN client incl. the IoT VLAN. Now u/p/t/s/apiKey are
redacted there; a redacted URL sent back is swapped for the real one held by
the bridge. Still open: `/v1/status?session_id=` returns real URLs (Tempo
needs them to send queue edits back); a full fix is Tempo sending only song
ids and the bridge building stream URLs with its own credential.

## Fallback

If the Shield snapclient is unreliable, job 2 can use Cast: enable MA's
Chromecast provider, filter the bridge to the Shield. Casting wakes the
Shield and TV itself. No sync, and Google's generic receiver UI. Also check
whether MA 2.9.x's Sendspin has a usable Android client before the MA upgrade
(`music-assistant-upgrade-plan.md`); not verified.

## Left out

Our own Snapcast protocol client; moving the kitchen jukebox to Snapcast;
syncing squeezelite with Snapcast; MA library browsing; a Google Cast receiver
app (needs Google's cloud); playlist editing, downloads, lyrics; video
lip-sync; the downstairs Chromecast with Google TV until the Shield version
works.
