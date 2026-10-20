# Music Assistant upgrade check

Before/after harness for the 2.6.3 -> 2.10.x upgrade
(`../music-assistant-upgrade-plan.md`). Stdlib Python, run from this folder.

```bash
python3 ma_check.py capture pre-upgrade     # snapshot -> snapshots/pre-upgrade.json (done 2026-10-20 on 2.6.3)
python3 ma_check.py smoke                   # silent checks, compared with snapshots/pre-upgrade.json
python3 ma_check.py smoke --audio           # + quiet Simon Room tests (6 %, a 2 s chime, ~3 s hum)
python3 ma_check.py capture post-upgrade && python3 ma_check.py diff pre-upgrade post-upgrade
```

Silent checks: MA reachable + version; every baseline player present and
available; the Node-RED Amp Speakers subflow's hardcoded player ids exist;
kitchen + Simon queues; library search through the Navidrome provider;
snapcast/squeezelite/opensubsonic providers loaded; snapserver zone clients
connected; HA `music_assistant` entities not newly unavailable; each
consumer's own endpoint (Tempo bridge, tts-router, NFC jukebox, voice
orchestrator, amp-lease-manager); snapserver did not crash during the run
(start time + SEGV count).

Audio checks (Simon Room only; volume read from and restored at the
snapserver, retried if it is restarting): MA volume_set reaches the
snapclient; an announcement clears `announcement_in_progress` and puts the
group back on `default`; play + stop.

**Baseline on 2.6.3 (2026-10-20 09:47):** 14/14 silent checks pass. With
`--audio`, volume and announcement pass, and **play + stop crashes the
snapserver** (SIGSEGV in `Stream.RemoveStream` of the live music stream; the
announcement stream removed after its feeder finished did not crash). That is
the bug the upgrade should fix: after upgrading, `smoke --audio` must pass
17/17 with the SEGV count unchanged.

Secrets are read from their files and never printed: MA token from
`/home/pi/home-audio-adapter/.env`, HA token from
`/home/pi/cecret_lake/dashboard_webapp/ha_token`. Tones are generated into
/tmp and served on :8812 only while the audio tests run.

Not automated (do by hand after the upgrade, in the plan's per-consumer list):
a jukebox card scan, one voice music command, a doorbell announcement, the
bedtime inject, kitchen volume.

**On the patched 2.6.3 (2026-10-20 16:48):** `smoke --audio` 17/17 three
times, no crash. The players check now only expects players that were
available in the baseline (MA drops idle browser "This Device" players on
restart).
