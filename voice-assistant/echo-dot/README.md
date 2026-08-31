# Echo Dot 2 satellite

This is a thin EchoMuse transport adapter around the existing Voice PE bridge.
The rooted Dot supplies microphones, speaker, LEDs, and buttons; the Beelink
continues to own LiveKit wake detection, Parakeet verification, Silero VAD,
intent handling, and TTS.

The standalone Compose project requests `active`, but active mode additionally
requires the separate `data/routed-audio-armed` interlock, matching the Voice
PE safety shape. Delete that runtime file to make a restart fall back to
`shadow`; shadow scores without feedback/actions, while `probe` adds silent
Parakeet stage-2 checks.

Endpoints on the Beelink:

- EchoMuse WebSockets and mDNS target: TCP 8770
- health and mode control: `http://127.0.0.1:8796`

The satellite id was `office-dot` for the first live test in the office
(2026-08-30). Since 2026-08-31 it is `master`: the Dot replaced the Pi 4 as the
master closet microphone, so every room-scoped thing keyed on that id (the
bath blind, `master_lights_hold`, the amp-zone reply/alarm route, timers)
followed it without edits. Result the same day: the Dot verified 100% from the
master bath, where the Pi's USB mic missed about half — so the Pi was retired
(unplugged, `master-pi` entry removed, its :1881 monitoring nodes disabled).

The device launcher in `device/start-echo-dot-satellite.sh` is suitable for
Magisk `service.d`. This Dot has Magisk 17.3, whose persistent script directory
is `.core/service.d` inside `/data/adb/magisk.img` (mounted at
`/sbin/.core/img/.core/service.d`). Magisk 18+ instead uses
`/data/adb/service.d`; copies are installed in both locations for compatibility.
It intentionally skips EchoMuse's stock `echoaudio` wait:
Amazon speech services are disabled on this repurposed unit and waiting for
that process would delay the local satellite by four minutes on every boot.
