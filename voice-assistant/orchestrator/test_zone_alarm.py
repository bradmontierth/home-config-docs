"""The zone ring's two silent failure modes, both found live on 2026-08-08.

A zone alarm plays through the whole-home amp, and everything that reaches
those speakers through Node-RED gets padded by the pad service (measured:
1.61s of speech inside a 5.52s file). Ours went straight to Music Assistant
with 0.36s of tail and the ends of words were clipped in the room.

The other one is the phone alert, which told Brad his master bath timer was
"ringing in the kitchen" — an alert that sends you to the wrong floor is
arguably worse than no alert.
"""

import asyncio
import os
import struct
import tempfile
import unittest
import wave
from unittest.mock import AsyncMock, patch

from . import config, zone_alarm, zones


def _write_wav(path: str, seconds: float, rate: int = 24000) -> None:
    """A TTS announcement as our synthesiser actually writes one.

    It streams, so it cannot know the length up front and stamps the "unknown"
    sentinel into the size fields — wave then reports nframes = 2147483647.
    Copying that count into the padded file overflows the RIFF size field on
    close, which is exactly how the first version of this shipped: the tests
    passed against tidy handwritten WAVs and the live bath alarm went out
    unpadded. Write the ugly shape here so the tests can catch it.
    """
    body = b"\1\0" * int(seconds * rate)
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, rate, rate * 2, 2, 16)
    header = (b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVE" + fmt
              + b"data" + struct.pack("<I", 0xFFFFFFFE))
    with open(path, "wb") as fh:
        fh.write(header + body)


def _duration(path: str) -> float:
    with wave.open(path) as fh:
        return fh.getnframes() / fh.getframerate()


class PaddedAnnouncementTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        patcher = patch.object(config, "ANNOUNCE_CACHE_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pad_adds_real_silence_to_the_tail(self):
        _write_wav(os.path.join(self.dir, "abc123.wav"), 1.10)
        name = zone_alarm.padded_announcement("abc123")
        self.assertEqual(name, "abc123-zone.wav")
        padded = _duration(os.path.join(self.dir, name))
        self.assertAlmostEqual(padded, 1.10 + zone_alarm.ANNOUNCE_PAD_S, places=2)

    def test_streaming_source_header_does_not_leak_into_the_copy(self):
        """The sentinel frame count must not survive the copy — carrying it
        over is what overflowed the header and dropped the padding live."""
        _write_wav(os.path.join(self.dir, "abc123.wav"), 1.10)
        name = zone_alarm.padded_announcement("abc123")
        with wave.open(os.path.join(self.dir, name)) as fh:
            self.assertLess(fh.getnframes(), 1 << 24)

    def test_original_is_left_alone_for_the_satellite_path(self):
        """The kitchen satellite plays this same wav and then loops its ring
        with its own 2s gap; padding in place would just be dead air there."""
        src = os.path.join(self.dir, "abc123.wav")
        _write_wav(src, 1.10)
        before = open(src, "rb").read()
        zone_alarm.padded_announcement("abc123")
        # Byte comparison, not duration: the source carries the streaming
        # sentinel, so wave reports its length as about 25 hours.
        self.assertEqual(open(src, "rb").read(), before)

    def test_second_call_reuses_the_cached_copy(self):
        _write_wav(os.path.join(self.dir, "abc123.wav"), 1.10)
        first = zone_alarm.padded_announcement("abc123")
        out = os.path.join(self.dir, first)
        stamp = os.stat(out).st_mtime_ns
        self.assertEqual(zone_alarm.padded_announcement("abc123"), first)
        self.assertEqual(os.stat(out).st_mtime_ns, stamp)

    def test_missing_announcement_returns_none_not_a_broken_url(self):
        """The caller falls back to the unpadded URL on None. Returning a name
        for a file that does not exist would ring the room in silence."""
        self.assertIsNone(zone_alarm.padded_announcement("nope"))

    def test_unreadable_announcement_returns_none(self):
        with open(os.path.join(self.dir, "junk.wav"), "wb") as fh:
            fh.write(b"not a wav at all")
        self.assertIsNone(zone_alarm.padded_announcement("junk"))

    def test_a_failed_pad_leaves_nothing_behind_to_be_cached(self):
        """os.path.exists() is the cache, so a stub left by a failure would be
        served as a valid announcement from then on — the room would ring in
        silence and nothing would say why."""
        with open(os.path.join(self.dir, "junk.wav"), "wb") as fh:
            fh.write(b"not a wav at all")
        zone_alarm.padded_announcement("junk")
        self.assertEqual([f for f in os.listdir(self.dir) if "zone" in f], [])


class SpokenRoomTest(unittest.TestCase):
    def test_table_name_wins(self):
        with patch.object(zones, "_entry", return_value={"spoken": "master bath"}):
            self.assertEqual(zones.spoken_for("master"), "master bath")

    def test_falls_back_to_the_satellite_id(self):
        with patch.object(zones, "_entry", return_value={}):
            self.assertEqual(zones.spoken_for("simon"), "simon")

    def test_untabled_satellite_still_names_a_room(self):
        with patch.object(zones, "_entry", return_value=None):
            self.assertEqual(zones.spoken_for("loft"), "loft")

    def test_timer_predating_the_sat_column_reads_as_kitchen(self):
        """Rows with a NULL sat really were all kitchen timers."""
        self.assertEqual(zones.spoken_for(None), "kitchen")


class RestingVolumeTest(unittest.TestCase):
    """The room must never be handed back silent, and never handed back at
    alarm volume. The second one is the ratchet: MA restores its cached
    pre-announcement volume when an announcement ends, and that cache holds
    the level the last ring set."""

    ROUTE = {"snap_client": "shower", "ma_player": "ma_shower",
             "volume": 20, "alarm_volume": 45}

    def _resting(self, snap=None, ma=None, route=None):
        with patch.object(zone_alarm, "_snap_volume", new=AsyncMock(return_value=snap)), \
             patch.object(zone_alarm, "_player_volume", new=AsyncMock(return_value=ma)):
            return asyncio.run(zone_alarm._resting_volume(route or self.ROUTE))

    def test_normal_reading_is_kept(self):
        self.assertEqual(self._resting(snap=30), 30)

    def test_alarm_volume_read_back_is_refused(self):
        self.assertEqual(self._resting(snap=45), 20)

    def test_anything_above_alarm_volume_is_refused_too(self):
        self.assertEqual(self._resting(snap=60), 20)

    def test_zero_never_survives(self):
        """MA reported ma_shower at 0 for hours while it was really at 20."""
        self.assertEqual(self._resting(snap=0, ma=0), 20)

    def test_falls_through_to_ma_then_to_config(self):
        self.assertEqual(self._resting(snap=None, ma=25), 25)
        self.assertEqual(self._resting(snap=None, ma=None), 20)

    def test_route_without_an_alarm_volume_keeps_the_reading(self):
        route = {"snap_client": "loft", "ma_player": "ma_loft", "volume": 50}
        self.assertEqual(self._resting(snap=70, route=route), 70)


class RingOrderingTest(unittest.TestCase):
    def test_wake_gate_is_long_enough_to_lose_the_race(self):
        """zone_alarm POSTs MA directly while the amp wake goes MQTT -> HA ->
        Node-RED -> isolate -> MA. Measured at 11ms on 2026-08-08, but nothing
        holds it there; lose and the announcement plays into a sleeping amp
        with the wake tone queued behind it."""
        self.assertGreaterEqual(zone_alarm.WAKE_GATE_S, 0.5)
