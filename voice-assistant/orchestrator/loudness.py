"""How loud a wake clip is — the paired-mic evidence for room attribution.

Two microphones share the open kitchen/family-room space, and when both hear
a wake the one that reaches /verify first owns the turn. That is a latency
race (the kitchen runs the smaller stage-1 hop and wins 82% of contested
wakes), not a statement about which room the speaker is in. Distance IS
audible, though: the nearer mic gets the wake phrase louder. This module
measures that, on the pre-roll both mics already send, so the turns table can
accumulate winner/loser loudness pairs without touching the chime path.

The number is the RMS of the loudest WINDOW_MS window in the clip, in dBFS.
Peak-window rather than whole-clip so a long quiet lead-in (the pre-roll is
~2.5 s and the wake phrase ~0.7 s of it) does not dilute the reading, and RMS
rather than peak so one click or clap does not set it.

Pure Python on purpose: the orchestrator image has no numpy, audioop is
deprecated, and 40k samples take a few milliseconds. Callers run it in a
background task after the /verify response is on the wire.
"""

from __future__ import annotations

import io
import math
import wave
from array import array

WINDOW_MS = 500
BLOCK_MS = 50


def peak_window_dbfs(wav_bytes: bytes, window_ms: int = WINDOW_MS) -> float | None:
    """dBFS of the loudest `window_ms` of a 16-bit PCM WAV; None if unreadable
    or silent. Multi-channel audio is folded to its first channel."""
    try:
        with wave.open(io.BytesIO(wav_bytes)) as wav:
            if wav.getsampwidth() != 2:
                return None
            rate = wav.getframerate()
            channels = wav.getnchannels()
            frames = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError, ValueError):
        return None
    samples = array("h")
    samples.frombytes(frames[: len(frames) - len(frames) % 2])
    if channels > 1:
        samples = samples[::channels]
    if not samples:
        return None
    block = max(1, rate * BLOCK_MS // 1000)
    per_window = max(1, window_ms // BLOCK_MS)
    energies = [
        sum(x * x for x in samples[i:i + block])
        for i in range(0, len(samples), block)
    ]
    counts = [min(block, len(samples) - i) for i in range(0, len(samples), block)]
    best = 0.0
    for i in range(max(1, len(energies) - per_window + 1)):
        e = sum(energies[i:i + per_window])
        n = sum(counts[i:i + per_window])
        if n:
            best = max(best, e / n)
    if best <= 0:
        return None
    return round(20 * math.log10(math.sqrt(best) / 32768.0), 1)
