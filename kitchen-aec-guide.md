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

## Interim-state service map (delete after cutover)

- `/etc/modules-load.d/snd-aloop.conf`, `/etc/modprobe.d/snd-aloop.conf`
  (Loop card, index 10)
- `/etc/systemd/system/alsaloop-speakers.service`, `alsaloop-aecref.service`
- `/etc/systemd/system/audio-keepalive.service.d/aloop-chain.conf`
- `~/.asoundrc` blocks: `kitchenmix` (loop dmix), `loop_tap`, `speakers_out`,
  `aec_ref`
