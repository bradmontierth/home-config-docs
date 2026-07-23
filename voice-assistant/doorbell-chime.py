#!/usr/bin/env python3
"""Regenerate the kitchen doorbell chime WAV.

The Node-RED Doorbell tab (function "Build VLC + audio") POSTs
{"url": "http://192.168.10.217:8785/audio/doorbell.wav"} to the kitchen
satellite's /media/play — the same killable/auto-ducking relay the slideshow
uses — replacing the old squeezelite play_media of doorbell.mp3 that lived on
the decommissioned .250 box (2026-07-23).

The WAV is served by the orchestrator from its announce cache, which is
runtime data with no backup — if it goes missing, regenerate:

    python3 doorbell-chime.py
    docker cp doorbell.wav voice-orchestrator:/data/announce/doorbell.wav

(The host path /home/pi/voice-pipeline/data/announce/ is owned by the
container user, hence docker cp.)
"""
import math
import struct
import wave

RATE = 44100


def strike(freq: float, dur: float, amp: float) -> list[float]:
    """One bell strike: fundamental + soft partials, fast attack, exp decay."""
    n = int(RATE * dur)
    out = []
    partials = [(1.0, 1.0), (2.76, 0.35), (5.4, 0.12)]  # bell-ish overtones
    for i in range(n):
        ts = i / RATE
        env = min(1.0, ts / 0.008) * math.exp(-ts / 0.5)
        s = sum(a * math.sin(2 * math.pi * freq * m * ts) *
                math.exp(-ts * (m - 1) * 2.2)            # partials die faster
                for m, a in partials)
        out.append(amp * env * s)
    return out


def main() -> None:
    dur = 2.4
    buf = [0.0] * int(RATE * dur)
    for start, tone in ((0.0, strike(659.26, 2.0, 0.42)),     # E5 "ding"
                        (0.55, strike(523.25, 1.85, 0.42))):  # C5 "dong"
        o = int(RATE * start)
        for i, v in enumerate(tone):
            if o + i < len(buf):
                buf[o + i] += v
    peak = max(abs(v) for v in buf)
    pcm = b"".join(struct.pack("<h", int(v / peak * 0.7 * 32767)) for v in buf)
    with wave.open("doorbell.wav", "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    print(f"wrote doorbell.wav ({len(pcm) // 2} samples, {dur}s)")


if __name__ == "__main__":
    main()
