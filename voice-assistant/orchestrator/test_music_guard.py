"""A room's effect buttons are named like songs; the classifier can't see the
room's table (live 2026-08-27 and 2026-08-31: "sorry i didnt touch that give
me pac man" -> play_music -> album "Piano Man" at 75; "give me ocean waves i
like that" -> play_music -> refused). A near-exact room alias for the query or
the whole command overrules play_music. Nothing here touches a network."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from . import app, home_control


class PlayMusicHomeGuardTest(unittest.TestCase):
    def _run(self, phrase: str, query: str, media_type=None, sat="simon"):
        press = AsyncMock()
        play = AsyncMock(side_effect=LookupError)
        parsed = app.intent_mod.validate({"intent": "play_music", "query": query,
                                          "media_type": media_type})
        token = app._CUR_SAT.set(sat)
        self.addCleanup(app._CUR_SAT.reset, token)
        with (
            patch.object(app.intent_mod, "parse", new=AsyncMock(return_value=parsed)),
            patch.object(app.home_mod, "_press", new=press),
            patch.object(app.music_mod, "play", new=play),
            patch.object(app, "_speak_reply", new=AsyncMock()),
            patch.object(app.broadcast_mod, "send", new=AsyncMock()),
            patch.object(app.events, "emit", new=AsyncMock()),
        ):
            result = asyncio.run(app.handle_command(phrase))
        return result, press, play

    def test_strong_match_reads_the_room_table(self):
        self.assertEqual(home_control.strong_match("pac man", "simon"),
                         "simon_fx_pac_man")
        self.assertEqual(home_control.strong_match("ocean waves", "simon"),
                         "simon_fx_ocean_waves")
        self.assertIsNone(home_control.strong_match("piano man", "simon"))
        self.assertIsNone(home_control.strong_match("pac man", "kitchen"))
        self.assertIsNone(home_control.strong_match(None, "simon"))

    def test_song_like_effect_name_presses_the_button_not_the_album(self):
        result, press, play = self._run(
            "sorry i didnt touch that give me pac man", "pac man")
        self.assertEqual(result["intent"], "home_control")
        self.assertTrue(result["ok"])
        play.assert_not_awaited()
        press.assert_awaited_once_with(
            home_control._commands()["simon_fx_pac_man"]["entity"])

    def test_trailing_chatter_does_not_lose_the_effect(self):
        result, press, play = self._run(
            "give me ocean waves i like that", "ocean waves")
        self.assertEqual(result["intent"], "home_control")
        play.assert_not_awaited()
        press.assert_awaited_once_with(
            home_control._commands()["simon_fx_ocean_waves"]["entity"])

    def test_a_real_music_request_still_plays_music(self):
        result, press, play = self._run(
            "play the album piano man", "piano man", media_type="album")
        self.assertEqual(result["intent"], "play_music")
        play.assert_awaited_once()
        press.assert_not_awaited()

    def test_the_guard_is_room_scoped(self):
        """The kitchen has no pac man button; 'pac man' there is a music
        query like any other."""
        result, press, play = self._run("play pac man", "pac man", sat="kitchen")
        self.assertEqual(result["intent"], "play_music")
        play.assert_awaited_once()
        press.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
