import math
import struct
import unittest

from . import loudness


def _wav(samples: list[int], rate: int = 16000, channels: int = 1) -> bytes:
    data = struct.pack(f"<{len(samples)}h", *samples)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(data), b"WAVE", b"fmt ", 16, 1,
        channels, rate, rate * channels * 2, channels * 2, 16, b"data", len(data))
    return header + data


def _tone(seconds: float, amplitude: int, rate: int = 16000) -> list[int]:
    n = int(seconds * rate)
    return [int(amplitude * math.sin(2 * math.pi * 440 * i / rate)) for i in range(n)]


class PeakWindowTest(unittest.TestCase):
    def test_full_scale_sine_is_minus_three_db(self):
        db = loudness.peak_window_dbfs(_wav(_tone(1.0, 32767)))
        self.assertAlmostEqual(db, -3.0, delta=0.2)

    def test_quiet_lead_in_does_not_dilute_the_wake(self):
        # 2 s of near-silence then 0.6 s of speech-level tone: the reading is
        # the loud part, not the average of the clip.
        clip = [0] * 32000 + _tone(0.6, 3277)   # -23 dBFS peak, -20 dBFS RMS
        db = loudness.peak_window_dbfs(_wav(clip))
        self.assertAlmostEqual(db, -23.0, delta=0.6)

    def test_nearer_mic_reads_louder(self):
        near = loudness.peak_window_dbfs(_wav(_tone(0.8, 8000)))
        far = loudness.peak_window_dbfs(_wav(_tone(0.8, 2000)))
        self.assertAlmostEqual(near - far, 12.0, delta=0.2)

    def test_silence_and_garbage_are_none(self):
        self.assertIsNone(loudness.peak_window_dbfs(_wav([0] * 16000)))
        self.assertIsNone(loudness.peak_window_dbfs(b"not a wav"))
        self.assertIsNone(loudness.peak_window_dbfs(b""))

    def test_stereo_folds_to_one_channel(self):
        mono = _tone(0.5, 8000)
        stereo = [x for x in mono for _ in (0, 1)]
        self.assertEqual(loudness.peak_window_dbfs(_wav(mono)),
                         loudness.peak_window_dbfs(_wav(stereo, channels=2)))
