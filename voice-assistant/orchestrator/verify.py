"""Stage-2 wake verification + command extraction.

The satellite ships the whole utterance ("okay computer set a chicken timer
for twelve minutes"). Parakeet transcribes it once; we then (a) confirm the
wake phrase really is present near the start (rejecting stage-1 false accepts,
proven 13/13 on real household clips) and (b) return the command tail for
intent parsing.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from . import config

_NORM_RE = re.compile(r"[^a-z0-9 ]+")


def _normalize(text: str) -> str:
    return _NORM_RE.sub(" ", text.lower()).strip()


def verify_and_extract(transcript: str) -> tuple[bool, str, float]:
    """Return (verified, command, score).

    Slides a window the length of the wake phrase across the first several
    words of the transcript; the best partial_ratio must clear the threshold.
    The command is everything after the matched wake phrase.
    """
    norm = _normalize(transcript)
    if not norm:
        return False, "", 0.0

    words = norm.split()

    best_score = 0.0
    best_end = 0
    for raw_phrase in config.WAKE_PHRASES:
        phrase = _normalize(raw_phrase)
        n = len(phrase.split())
        # Only look in the leading region — the wake word comes first. Allow a
        # small lead-in (mis-transcribed noise before "okay") and +/-1 word slop.
        search_limit = min(len(words), n + 4)
        for start in range(search_limit):
            for width in (n - 1, n, n + 1):
                if width <= 0 or start + width > len(words):
                    continue
                candidate = " ".join(words[start : start + width])
                score = fuzz.ratio(candidate, phrase)
                if score > best_score:
                    best_score = score
                    best_end = start + width

    verified = best_score >= config.WAKE_FUZZ_THRESHOLD
    command = " ".join(words[best_end:]).strip() if verified else ""
    return verified, command, round(best_score, 1)
