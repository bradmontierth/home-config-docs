# Kitchen AEC — "okay computer" over music (build record + cutover guide)

**CUTOVER DONE 2026-08-19 night.** Brad moved the amp aux to the array's
3.5mm (accidentally power-cycling the mini PC via its lookalike barrel plug
— everything came back clean on its own, which also proved the boot
ordering). Post-cutover verification: ~30 dB cancellation, **flat for 45+ s
— the 6-second collapse is gone** (same-clock topology, filter even retains
convergence across sessions). Two gotchas found and fixed during cutover:

- **The array's USB-audio mixer volume** (`amixer -c Array`, 'PCM') is
  initialized by ALSA at −20 dB after flash/re-enumeration → the line-out
  was near-silent and looked exactly like a dead jack (known XMOS "low
  playback volume on Linux" issue). Set to 0 dB + `sudo alsactl store Array`
  (persisted). If the array is ever re-flashed or replugged and goes quiet,
  check this FIRST.
- ES8336 `Speaker` switch forced off post-cutover (the mini PC's internal
  lo-fi speaker; the power cycle resets its GPIO to amp-on — nothing routes
  audio to the ES8336 anymore, but keep it muted anyway).

AIC3104 HP/line-out levels left at Seeed's tuned default 8 (they reset to 8
on power cycle anyway; only `SAVE_CONFIGURATION` persists on-chip changes).
Overall chain gain differs from the ES8336 days — announcement/jukebox
volume calibration (65) may want an ear-retune. Pending: Brad's live
"okay computer over music" test + a day of voice-ops stage-1/2 rates.

**Built 2026-08-19 (config side complete; one physical step pending).**
Goal: backlog item 4 — wake word while music plays on the kitchen big
speakers. Approach: give the XVF3800's on-chip AEC the far-end reference it
was missing, so `respeaker_ch0` (the processed Conference beam the satellite
already consumes) comes out with the music cancelled. No satellite changes.

## The one thing left to do (Brad, ~2 minutes)

Move the amp's 3.5mm plug from the **mini PC's ES8336 jack** to the
**reSpeaker XVF3800's line-out jack** (3.5mm next to its USB-C port), then:

```
ssh big-speaker-mini-pc
./aec-cutover.sh        # repoints kitchenmix at the array, verifies AEC
```

The script smoke-plays through the speakers, then runs a 30s cancellation
check and prints GOOD/BAD criteria. Rollback = move the plug back + run
`./aec-rollback.sh`. After a good cutover, say "okay computer" over loud
music as the real acceptance test, and watch stage-1/stage-2 rates on the
voice-ops dashboard for a day.

## Why the cable move (what we learned the hard way)

The obvious build — keep speakers on the ES8336 and mirror `kitchenmix`
into the array's USB playback as a reference — was built (snd-aloop +
two adaptive-resample `alsaloop` legs, currently LIVE as the interim state)
and **the AEC engaged: ~35 dB of cancellation, down to the room noise
floor… for about 6 seconds after each far-end onset, then it collapsed to
NS-only (~7 dB) and never recovered** while the signal continued. Chased it
through: leg-latency sweeps (alignment sweet spot found at ref-leg 60 ms —
±20 ms filter window, acausal side = instant death), `AUDIO_MGR_SYS_DELAY`
(range is only −64..+256 samples = 16 ms, can't bridge an external path),
`AEC_FAR_EXTGAIN`, PCD flags, beam-azimuth polling (the free beam thrashes
between the two stereo speakers at 88°/355° — my mono-duplicated test noise
is pathological for DoA, but it wasn't the killer). Conclusion: the SHF
filter cannot track two free-running clocks (ES8336 vs XMOS, content slides
~0.5 ms by ~6 s = full decorrelation at 16 kHz), and adaptive resampling in
the legs bounds long-term drift but not the wander that kills a converged
filter. **The chip wants its reference and its speaker on the same clock —
so make the array the speaker DAC.** That's the topology every firmware
default (SYS_DELAY 12) is factory-tuned for.

## What was done tonight (all on big-speaker-mini-pc / .251)

1. **Firmware**: flashed the XVF3800 from v2.0.6 (16 kHz USB) to
   **v2.1.0_48k2ch** (48 kHz USB in/out) via
   `sudo dfu-util -R -e -a 1 -D .../respeaker_xvf3800_usb_dfu_firmware_v2.1.0_48k2ch.bin`
   (repo clone at `/home/pi/reSpeaker_XVF3800_USB_4MIC_ARRAY`, old images
   there for revert). The array's line-out is a TI AIC3104 at 48 kHz stereo,
   default 8 dB out — real music quality, not the 16 kHz of the old firmware.
   Capture side now enumerates 48 kHz; the `respeaker_ch0` plug resamples to
   the satellite's 16 kHz transparently. **Verified post-flash**: satellite
   healthy, wake CONFIRMED score=100 / chime 230 ms on a replayed
   verify-ok clip through the speakers.
2. **Interim topology (LIVE now, until the cable move)**: `kitchenmix` →
   dmix on `snd-aloop` → `alsaloop-speakers.service` (→ ES8336, `-t 30000`)
   + `alsaloop-aecref.service` (→ array USB playback, `-t 60000`), both
   `-S 5` adaptive sync; `audio-keepalive` drop-in `aloop-chain.conf` orders
   them first. Same audio behavior as before the build (NS-only during
   music, ~30 ms extra speaker latency); no regression, wake replay passes.
3. **Cutover prepared**: `/home/pi/aec-cutover.sh` (repoints `kitchenmix` →
   dmix on `hw:CARD=Array` @48k, retires the aloop legs, keepalive then
   holds the array stream open 24/7 — pop-fix + clock-keeper in one) and
   `/home/pi/aec-rollback.sh`. `~/.asoundrc.pre-aec` = pre-build copy.

## Post-cutover notes

- Everything already funnels through the `kitchenmix` ALSA name
  (squeezelite `-o kitchenmix`, satellite `PLAYBACK_DEVICE=kitchenmix`,
  keepalive), so music, chimes, TTS, alarms, and media relays all land in
  the reference automatically — all cancellable, zero app changes.
- Output level: AIC3104 line-out defaults to 8 dB (range 0–9,
  `AIC3104_LINEOUT_LEVEL` — needs a newer `xvf_host` than the repo copy,
  which doesn't know the v2.1.0 commands yet; use the amp knob first).
- Tuning tool: `cd /home/pi/reSpeaker_XVF3800_USB_4MIC_ARRAY/host_control/linux_x86_64
  && sudo ./xvf_host <CMD>`. Useful: `AUDIO_MGR_SYS_DELAY` (start at factory
  12; range −64..256 samples @16k), `AEC_RT60`, `--dump-params`.
  `SAVE_CONFIGURATION` persists tuning to flash (v2.1.0 feature).
- The ES8336 path stays intact but idle (fallback via `speakers_out` PCM +
  rollback script). The `speaker-gpio-pulse` keepalive drop-in becomes a
  harmless no-op.
- **Wake-bench replay caveat**: once AEC is live, replaying wake clips
  through the big speakers self-defeats — the clip is in the reference and
  gets cancelled (also why music vocals can't false-wake). Bench via a
  phone/portable speaker instead, or stop `audio-keepalive` + use
  `speakers_out`… no — after cutover `speakers_out` isn't wired to the amp;
  use an external sound source.
- Expected result: near-total echo cancellation (we measured to the noise
  floor in the aligned windows), but wake-over-*loud* music is still
  physics-limited — mics sit right next to the speakers. If recall over
  music is good but not great, next knobs: `AUDIO_MGR_SYS_DELAY` fine-tune,
  fixed beams (`AEC_FIXEDBEAMS*`) pointed into the room instead of the
  auto-beam that chases the speakers.

## Post-chime first-word loss (found + fixed 2026-08-26)

**Symptom:** since the cutover the kitchen dropped the first word of a
command spoken right after the confirm chime — 8 of 40 kitchen commands
08-20→08-26 (`three minutes to my call fire timer`, `bradley today to roast
coffee`, `glare`, `timer for five minutes`…) vs 0 of 30 the week before and
10/10 clean on the family room (no AEC). Brad listened to the clips
(http://192.168.10.251:8782/): the word is simply absent from the audio.

**Cause:** the far end isn't music, it's the **chime**. `respeaker_ch0` is
the comms output with full post-processing, and the residual-echo
suppressor was at its VoIP-conference defaults — `PP_DTSENSITIVE 0`
("prefers high echo suppression at the cost of doubletalk"), tail
over-subtraction `PP_GAMMA_ETAIL 1.0`, `PP_NLATTENONOFF 1`. After the chime
it keeps clamping for an estimated echo tail; a word spoken in that window
is treated as echo. (Pre-cutover the chime wasn't a far-end signal at all.)
Not ASR — Parakeet re-run on the clips gives the same text — and not the
satellite's spurious-onset guard.

**Fix:** `PP_DTSENSITIVE 13` (best double-talk + the extra near-end speech
detector that 2-digit values enable). Applied 2026-08-26 21:12 via
`~/aec-trial.sh step1` — 5/5 commands kept their first word, including two
"what time is it" right on the chime. **VOLATILE until `~/aec-trial.sh
save`** (SAVE_CONFIGURATION) — a power cycle of the array reverts to 0.
Remaining before save: wake-over-music check with music at normal volume.
Fallback ladder if it ever regresses: `step2` (+GAMMA_ETAIL 0.5), `step3`
(+NLATTENONOFF 0), `nuke` (+ECHOONOFF 0 — linear AEC only, ~30 dB).
Helper + clip browser sources: `voice-assistant/tools/kitchen-aec/`.

**2026-09-03 — still swallowing; step2 applied, listen-test tooling.**
Adrienne's "set a timer for fifteen minutes" at 16:58 came through as
`for fifteen minutes` → unclear ("Sorry, I didn't catch that", and the
LLM classifier on the GX10 took 14 s to say so). `PP_DTSENSITIVE 13` WAS
live and saved (mini PC up 14 days, `aec-trial.sh show` confirmed), so step1
is not enough. The cmd clip (`cmd-20260903-165844.wav`) shows 1.1 s of floor
after capture start and speech surfacing mid-sentence; the head of the
phrase was spoken 0.5–1.2 s after the chime ended and is absent from the
audio. Tally since step1: 1 hard loss in ~40 kitchen commands (vs ~5/25
before), so the fix cut it 5–10x but did not close it. The 08-26 "5/5" was
judged by transcript; the browser now shows one of those very test clips
(`cmd-20260826-211430`, "what time is it") with the floor held 5 dB down
right up to the first word — transcript survived, audio was damaged.
Adrienne is hit more than Brad: she speaks sooner and from farther away.

- `~/aec-trial.sh step2` applied 2026-09-03 17:20 (PP_GAMMA_ETAIL 1.0→0.5)
  — **FAILED**: Brad listened to the 18:20 "who let dogs out fast version"
  and 18:24 "what does the fox say" clips (both "play …" with the head
  gone, onset 1.3 s into capture). No trace of the ding survives the AEC;
  the clamp simply lingers ~1 s after the far end stops, and everyone waits
  for the ding, so it lands on the first word every time.
- `~/aec-trial.sh step3` applied 2026-09-03 ~18:45 (PP_NLATTENONOFF 1→0,
  the non-linear residual-echo attenuator = the clamp itself; DTSENSITIVE 13
  and GAMMA_ETAIL 0.5 stay). **VOLATILE** — `save` to keep, `step2` to go
  back. Listen test pending. Last rung: `nuke` (PP_ECHOONOFF 0, linear AEC
  only ~30 dB — expect music bleed into ASR).
- **step3 verdict (Brad, ~19:45):** "set a timer for five seconds" clean,
  "play crap wave" still just clipped the head of "play" (lag remains), and
  **cancel-during-music was noticeably worse** — NLATTEN is what makes
  wake-over-music work. The ladder trades the two against each other, so
  the two-stream path was built instead (below) and the array is back at
  the saved step1 state (`revert` + `step1`, 2026-09-03 20:03).
- **Measured 20:00 with the satellite stopped for 15 s** (chime through the
  big speakers, 2-ch 16 k capture, no talker): ch0 dips ~4 dB during the
  ding and recovers ~0.1 s after it; ch1 = `MUX_AEC_RESIDUALS 3` (linear
  AEC residual of mic 3, no post-processing) shows the chime's linear
  residual at ~−40 dBFS over a −50 dBFS floor and NO hangover — there is no
  suppressor on that path to hang. `AUDIO_MGR_OP_R 8 1` (user-chosen 1) was
  tried and is just a copy of ch0, so the "ASR output" is not reachable
  that way; ch1 stays on the residual. Recordings: `/tmp/chtest/` on .251.

### Two-stream capture (SHIPPED 2026-09-03 20:03, kitchen only)

`satellite/assistant.py`: `MIC_CHANNELS=2` opens the array stereo via
`plughw:CARD=Array`; a `StereoDemux` wrapper hands every existing reader
ch0 exactly as before (wake model, verify pre-roll, Silero endpointing,
stop/alarm barge-in, `/mark` ring) and keeps the same frames' ch1 as
`last_side`; `capture_command` appends `last_side` to the command buffer
when `CMD_CHANNEL=1`. So `/command/audio` gets ch0 pre-roll + ch1 command
(a level step at the seam — Parakeet doesn't care), and the ding's clamp on
ch0 can no longer remove anything from the command. `CMD_CHANNEL_GAIN`
(1.0) is a software gain on ch1 (no AGC there; floor ~10 dB below ch0 —
raise it if commands transcribe weak). Kitchen `.env` now:
`MIC_DEVICE=plughw:CARD=Array`, `MIC_CHANNELS=2`, `CMD_CHANNEL=1`,
`CMD_CHANNEL_GAIN=1.0`. Rollback: `.env.pre-2stream` + `assistant.py.pre-2stream`
in `~/voice-pipeline`, restart `voice-assistant`. Other satellites are
untouched (`MIC_CHANNELS` defaults to 1). Caveats: the 1 MB pipe is now
16 s of headroom instead of 32 (a >16 s ask overruns, but that backlog is
drained anyway); VAD still judges ch0, so a command spoken ENTIRELY inside
the clamp (~1 s, e.g. a lone "stop") can still endpoint as no speech.
Correction to the earlier note: the wake turn deliberately does NOT drain
after the chime, so the capture starts at the trigger and the ding sits
~0.4–1.4 s into it (grey band in the clip browser).

- Structural option if the ladder costs too much over music: the array has
  a separate ASR output path (`AEC_ASROUTONOFF` 1, `AEC_ASROUTGAIN` 1.0,
  lighter post-processing by design) and the USB R channel is freely
  routable (`AUDIO_MGR_OP_R`, currently `MUX_AEC_RESIDUALS 3` = linear AEC
  residual of mic 3, no PP). Point R at the ASR output (or leave the linear
  residual), add `pcm.respeaker_ch1` in `~/.asoundrc` (ttable.0.1), and
  capture commands from ch1 while wake stays on ch0. The XMOS docs are not
  in the on-box repo — the USER_CHOSEN_CHANNELS source index for the ASR
  output needs the XVF3800 user guide.
- Clip browser (http://192.168.10.251:8782/) now draws the 50 ms RMS
  envelope per clip: dotted line = capture start, yellow band = capture
  start → first speech, red = onset; `clamp` = floor in that band minus the
  clip's own tail floor (negative = suppressor holding the mic down);
  `?only=suspect` lists cmd clips whose transcript opens on a non-head word
  ("for", "a", "the", a number…) or whose floor was held ≥4 dB down until an
  onset 0.2–1.2 s in. Listen test protocol: say "set a timer for five
  minutes" starting ON the ding from where you normally stand, then play the
  clip and listen for "set a" inside the yellow band — judge by ear and by
  the band, not by the transcript. Source: `voice-assistant/tools/kitchen-aec/clip-browser.py`.

Found alongside (different bug, all satellites): the spurious-onset guard
in `capture_command` discarded any wake-turn command under 500 ms of voice
because the "decided on buffered audio" lag test is true for the whole
capture (the pipe backlog offset never shrinks). Five short commands in 30
days died as `no_speech_onset` ("what time is it", "fix the glare"). Fixed
by adding an onset-position window (`SPURIOUS_ONSET_WINDOW_MS`, 1000) with
tiny blips (< `MIN_VOICED_MS`) still spurious anywhere; deployed to the
kitchen and family room 2026-08-26 21:24.

## Interim-state service map (delete after cutover)

- `/etc/modules-load.d/snd-aloop.conf`, `/etc/modprobe.d/snd-aloop.conf`
  (Loop card, index 10)
- `/etc/systemd/system/alsaloop-speakers.service`, `alsaloop-aecref.service`
- `/etc/systemd/system/audio-keepalive.service.d/aloop-chain.conf`
- `~/.asoundrc` blocks: `kitchenmix` (loop dmix), `loop_tap`, `speakers_out`,
  `aec_ref`
