import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from music_assistant_models.enums import PlayerState

from . import config, music, music_log


def _isolate_music_log(test: unittest.TestCase) -> str:
    """Point music_log at a throwaway database for the duration of a test.

    play() records every resolution now, so without this the suite writes rows
    into the live orchestrator.db — which it did, once, before this existed."""
    path = tempfile.mkdtemp(prefix="music-log-test-") + "/test.db"
    patcher = patch.object(config, "DB_PATH", path)
    patcher.start()
    test.addCleanup(patcher.stop)
    music_log._db = None

    def _close():
        if music_log._db is not None:
            music_log._db.close()
        music_log._db = None
        if os.path.exists(path):
            os.unlink(path)

    test.addCleanup(_close)
    return path


def _entry(kind: str, name: str, artist: str | None, local: bool, uri: str,
           version: str = "", owned: bool | None = None) -> dict:
    """One library-index row, built exactly the way _refresh_index does.

    owned defaults to local; pass it explicitly for the two interesting cases —
    a Spotify-only entry (neither) and a builtin library playlist (owned but
    with no file of its own)."""
    norm = music._norm(name)
    titled = f"{name} {version}" if version else name
    full = music._norm(f"{artist} {titled}") if artist else music._norm(titled)
    return {"kind": kind, "name": name, "uri": uri,
            "norm": norm, "collapsed": music._collapse(norm),
            "skel": music._skeleton(norm),
            "full": full, "fullc": music._collapse(full),
            "ftoks": frozenset(full.split()),
            "artist": artist, "local": local,
            "owned": local if owned is None else owned}


# The library as it stood on 2026-08-05: a favorited Spotify-only single and
# the Andrew Gold tracks we actually own, all named the same thing.
SKELETONS = [
    _entry("album", "Spooky, Scary Skeletons", "Andrew Gold", False,
           "library://album/104"),
    _entry("track", "Spooky, Scary Skeletons", "Andrew Gold", True,
           "library://track/2431"),
    _entry("track", "Spooky, Scary Skeletons", "Andrew Gold", True,
           "library://track/2432", version="Undead Tombstone Remix"),
    _entry("album", "Halloween Howls Fun & Scary Music (Deluxe Edition)",
           "Andrew Gold", True, "library://album/61"),
    _entry("artist", "Andrew Gold", None, True, "library://artist/57"),
    # MA's builtin provider: drawn from the library, so owned without a file.
    _entry("playlist", "500 Random tracks (from library)", None, False,
           "builtin://playlist/random_tracks", owned=True),
]


# The Planets, as the library actually holds it: two recordings of the same
# movement under formal titles long enough that a spoken query explains only a
# couple of their tokens, plus a title that contains the word "by".
JUPITER = [
    _entry("track", "(1914) The Planets, Op. 32 (4. Jupiter, the Bringer of Jollity)",
           "Gustav Holst", True, "library://track/711"),
    _entry("track", "Holst: The Planets, Op. 32: IV. Jupiter, the Bringer of Jollity",
           "Berliner Philharmoniker, Herbert von Karajan, Gustav Holst", True,
           "library://track/712"),
    _entry("artist", "Gustav Holst", None, True, "library://artist/57"),
    _entry("album", "100 Best Ever Pieces of the Greatest Classical Music",
           "Gustav Holst", True, "library://album/9"),
    _entry("track", "Stand By Me", "Ben E. King", True, "library://track/900"),
]


class ConnectorAndThresholdTest(unittest.TestCase):
    """The 2026-08-06 "play Jupiter by Holst" miss and the bars it exposed."""

    def setUp(self):
        patcher = patch.object(music, "_ensure_index",
                               new=AsyncMock(return_value=JUPITER))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _resolve(self, query: str, **kw):
        return asyncio.run(music._resolve_library(query, None, **kw))

    def test_a_by_connector_no_longer_costs_the_match(self):
        # The original failure: "by" is a query-only token, so token_set_ratio
        # dropped 100 -> 89.66 and the coverage weight took the result to
        # 79.65 against a bar of 80. The whole request was refused over a third
        # of a point.
        hit = self._resolve("jupiter by holst")
        self.assertEqual(hit["uri"], "library://track/711")
        self.assertGreater(hit["score"], 85)

    def test_the_connector_variant_cannot_lose_a_title_containing_by(self):
        # Variants are combined with max(), so dropping "by" can only ever RAISE
        # a score — a song whose title needs the word still matches on the
        # original variant.
        hit = self._resolve("stand by me")
        self.assertEqual(hit["uri"], "library://track/900")
        self.assertEqual(hit["score"], 100)

    def test_a_loose_match_now_plays_instead_of_refusing(self):
        # A stray ASR word ("thing") lands this at ~72: refused under the old
        # 80/85 bars, played under 70. The bounds are asserted so that a future
        # scoring change which moves this case out of the interesting band
        # fails here loudly rather than silently stops testing the bar.
        hit = self._resolve("holst jupiter thing")
        self.assertEqual(hit["uri"], "library://track/711")
        self.assertGreaterEqual(hit["score"], 70)
        self.assertLess(hit["score"], 80)

    def test_only_the_album_and_track_bars_were_lowered(self):
        # Playlist and artist are checked BEFORE album/track, so loosening them
        # would not recover misses, it would steal them into a shuffle.
        self.assertEqual(music._LIB_THRESHOLDS,
                         {"playlist": 92, "artist": 80, "album": 70, "track": 70})

    def test_the_trace_carries_the_ranking_even_when_nothing_qualifies(self):
        trace: dict = {}
        self.assertIsNone(self._resolve("baby shark", trace=trace))
        self.assertTrue(trace["candidates"])
        # Sorted best-first, and every entry says which bar it had to clear.
        scores = [c["score"] for c in trace["candidates"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertLess(scores[0], 70)
        self.assertTrue(all("bar" in c for c in trace["candidates"]))


class MusicLogTest(unittest.TestCase):
    """Every resolution lands in the durable table — refusals especially."""

    def setUp(self):
        self.queues = AsyncMock()
        self.client = AsyncMock()
        self.client.player_queues = self.queues
        for target, kw in (("_ma", {"return_value": self.client}),
                           ("_ensure_index", {"new": AsyncMock(return_value=JUPITER)}),
                           ("_notify_jukebox_takeover", {"new": AsyncMock()})):
            patcher = patch.object(music, target, **kw)
            patcher.start()
            self.addCleanup(patcher.stop)
        _isolate_music_log(self)

    def test_a_play_is_recorded_with_its_winner(self):
        asyncio.run(music.play("jupiter by holst"))
        row = music_log.recent()[0]
        self.assertEqual(row["decision"], "play")
        self.assertEqual(row["uri"], "library://track/711")
        self.assertEqual(row["via"], "library-index")
        self.assertTrue(row["candidates"])

    def test_a_refusal_records_how_close_it_got(self):
        # The point of the table: a refusal at 79 means the bar is wrong, one
        # at 40 means we simply don't own the song. That is only answerable if
        # the near-miss score lands in a real column rather than NULL.
        with self.assertRaises(LookupError):
            asyncio.run(music.play("baby shark"))
        row = music_log.recent()[0]
        self.assertEqual(row["decision"], "refuse")
        self.assertIsNotNone(row["score"])
        self.assertLess(row["score"], 70)
        self.assertEqual(row["query"], "baby shark")

    def test_recording_failure_never_costs_the_turn_its_song(self):
        with patch.object(music_log, "_conn", side_effect=RuntimeError("locked")):
            sel = asyncio.run(music.play("jupiter by holst"))
        self.assertEqual(sel["uri"], "library://track/711")


class ResolveLibraryTest(unittest.TestCase):
    """Bucket ranking, with the index stubbed to a fixed set of entries."""

    def setUp(self):
        patcher = patch.object(music, "_ensure_index",
                               new=AsyncMock(return_value=SKELETONS))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _resolve(self, query: str, **kw):
        return asyncio.run(music._resolve_library(query, None, **kw))

    def test_album_track_tie_goes_to_the_owned_files(self):
        # Album and track tie at 88.00 for the remix query and 100.00 for the
        # bare one; before the local tiebreak the Spotify-only album won on
        # list order and MA then had no tracks to play.
        for query in ("spooky scary skeletons remix", "spooky scary skeletons"):
            with self.subTest(query=query):
                hit = self._resolve(query)
                self.assertEqual(hit["kind"], "track")
                self.assertTrue(hit["local"])

    def test_a_spoken_version_qualifier_picks_that_version(self):
        # Identical track names — "Undead Tombstone Remix" lives in MA's
        # version field, which only reaches scoring via the token-set string.
        self.assertEqual(self._resolve("spooky scary skeletons remix")["uri"],
                         "library://track/2432")

    def test_an_unqualified_query_does_not_prefer_the_variant(self):
        self.assertEqual(self._resolve("spooky scary skeletons")["uri"],
                         "library://track/2431")

    def test_a_better_scoring_album_still_beats_a_local_track(self):
        # The tiebreak must only break TIES — score still leads.
        hit = self._resolve("halloween howls fun and scary music deluxe edition")
        self.assertEqual(hit["uri"], "library://album/61")

    def test_exclude_drops_a_uri_and_re_ranks(self):
        hit = self._resolve("spooky scary skeletons",
                            exclude={"library://track/2431"})
        self.assertEqual(hit["uri"], "library://track/2432")

    def test_exclude_can_empty_the_field(self):
        hit = self._resolve("spooky scary skeletons",
                            exclude={"library://track/2431",
                                     "library://track/2432"})
        self.assertIsNone(hit)

    def test_owned_only_never_resolves_an_online_entry(self):
        # The Spotify-only album outscores nothing here, but it must be
        # invisible even when it would have won outright.
        for query in ("spooky scary skeletons", "spooky scary skeletons remix"):
            with self.subTest(query=query):
                self.assertNotEqual(self._resolve(query)["uri"],
                                    "library://album/104")
        hit = self._resolve("spooky scary skeletons",
                            exclude={"library://track/2431",
                                     "library://track/2432"})
        self.assertIsNone(hit)

    def test_owned_only_still_allows_builtin_library_playlists(self):
        # No file of its own, but nothing foreign either.
        hit = self._resolve("500 random tracks from library")
        self.assertEqual(hit["uri"], "builtin://playlist/random_tracks")

    def test_online_entries_return_when_owned_only_is_off(self):
        with patch.object(config, "MUSIC_OWNED_ONLY", False):
            hit = self._resolve("spooky scary skeletons",
                                exclude={"library://track/2431",
                                         "library://track/2432"})
        self.assertEqual(hit["uri"], "library://album/104")


class PlayFallbackTest(unittest.TestCase):
    """play() re-ranks when MA refuses the URI the resolver picked."""

    def setUp(self):
        self.queues = AsyncMock()
        self.client = AsyncMock()
        self.client.player_queues = self.queues
        patcher = patch.object(music, "_ma", return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(music, "_ensure_index",
                               new=AsyncMock(return_value=SKELETONS))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(music, "_notify_jukebox_takeover", new=AsyncMock())
        patcher.start()
        self.addCleanup(patcher.stop)
        _isolate_music_log(self)

    def _played(self):
        return [c.args[1] for c in self.queues.play_media.await_args_list]

    def test_unplayable_winner_falls_back_to_the_runner_up(self):
        # MA's real failure mode: play_media raises "No playable items found"
        # for the Spotify album, and the owned track must still play.
        def reject_the_album(_qid, uri, **_kw):
            if uri == "library://album/104":
                raise RuntimeError("No playable items found")

        self.queues.play_media.side_effect = reject_the_album
        with patch.object(music, "_resolve_library",
                          side_effect=[SKELETONS[0] | {"score": 88.0},
                                       SKELETONS[1] | {"score": 88.0}]):
            sel = asyncio.run(music.play("spooky scary skeletons remix"))
        self.assertEqual(sel["uri"], "library://track/2431")
        self.assertEqual(self._played(),
                         ["library://album/104", "library://track/2431"])

    def test_a_playable_winner_is_not_retried(self):
        sel = asyncio.run(music.play("spooky scary skeletons"))
        self.assertEqual(sel["uri"], "library://track/2431")
        self.assertEqual(len(self._played()), 1)

    def test_failure_still_surfaces_when_nothing_is_playable(self):
        self.queues.play_media.side_effect = RuntimeError("No playable items found")
        with self.assertRaises(RuntimeError):
            asyncio.run(music.play("spooky scary skeletons"))
        # Winner + fallbacks, capped — never an unbounded retry storm.
        self.assertLessEqual(len(self._played()), music._PLAY_ATTEMPTS)

    def test_an_unowned_request_is_refused_without_searching(self):
        # The whole point: a song we don't have must not send MA off to find
        # a stranger's recording of it.
        with self.assertRaises(LookupError):
            asyncio.run(music.play("baby shark"))
        self.queues.play_media.assert_not_awaited()
        self.client.music.search.assert_not_awaited()

    def test_search_still_runs_when_owned_only_is_off(self):
        self.client.music.search.return_value = SimpleNamespace(
            playlists=[], artists=[], albums=[], tracks=[])
        with patch.object(config, "MUSIC_OWNED_ONLY", False):
            with self.assertRaises(LookupError):
                asyncio.run(music.play("baby shark"))
        self.client.music.search.assert_awaited()

    def test_shuffle_is_recomputed_for_the_fallback_kind(self):
        # Album rejected (in-order) -> artist fallback must shuffle.
        def reject_the_album(_qid, uri, **_kw):
            if uri == "library://album/104":
                raise RuntimeError("No playable items found")

        self.queues.play_media.side_effect = reject_the_album
        with patch.object(music, "_resolve_library",
                          side_effect=[SKELETONS[0] | {"score": 88.0},
                                       SKELETONS[4] | {"score": 88.0}]):
            sel = asyncio.run(music.play("spooky scary skeletons"))
        self.assertTrue(sel["shuffle"])
        self.assertEqual([c.args[1] for c in
                          self.queues.queue_command_shuffle.await_args_list],
                         [False, True])


class RoomPlaybackTest(unittest.TestCase):
    """Music in a room that is not the kitchen (master bath, 2026-08-09).

    Everything here is about the two rooms being genuinely different: the
    kitchen is a squeezelite box whose volume is somebody else's business, the
    bath is a snapclient behind an amp that sleeps, and Music Assistant lies
    about the volume of the second kind.
    """

    KITCHEN = {"queue": "kitchen-box", "local": True}
    BATH = {"queue": "ma_shower", "local": True, "snap_client": "shower",
            "volume": 20, "max_volume": 40, "rooms": ["shower"],
            "cap_minutes": 60}

    def setUp(self):
        self.queues = AsyncMock()
        self.players = AsyncMock()
        self.client = AsyncMock()
        self.client.player_queues = self.queues
        self.client.players = self.players
        # get() is synchronous on the real client; an AsyncMock child would
        # hand back a coroutine and every state check would read as truthy.
        self.idle = SimpleNamespace(state=PlayerState.IDLE)
        self.queues.get = Mock(return_value=self.idle)
        self.players.get = Mock(return_value=SimpleNamespace(volume_level=60))
        patcher = patch.object(music, "_ma", return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(music, "_ensure_index",
                               new=AsyncMock(return_value=SKELETONS))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(music, "_notify_jukebox_takeover", new=AsyncMock())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.wake = AsyncMock()
        patcher = patch("orchestrator.broadcast.amp_wake", new=self.wake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.snap = AsyncMock(return_value=20)
        patcher = patch.object(music.snapcast, "volume", new=self.snap)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Volume writes take one of two roads — the snapserver for an amp
        # zone, MA for the kitchen — so record both in one ordered list.
        self.writes: list[tuple[str, int]] = []
        self.snap_set = AsyncMock(
            side_effect=lambda cid, level: self.writes.append((cid, level)) or True)
        patcher = patch.object(music.snapcast, "set_volume", new=self.snap_set)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.players.player_command_volume_set = AsyncMock(
            side_effect=lambda qid, level: self.writes.append((qid, level)))
        music._duck.clear()
        music._caps.clear()
        self.addCleanup(music._duck.clear)
        self.addCleanup(music._caps.clear)
        _isolate_music_log(self)

    def _volumes(self):
        """Every volume write in order. Amp-zone writes are addressed to the
        snapclient ("shower"), kitchen writes to the MA player."""
        return self.writes

    def _playing(self):
        self.queues.get.return_value = SimpleNamespace(state=PlayerState.PLAYING)

    # -- where it plays ----------------------------------------------------
    def test_play_targets_the_rooms_own_queue(self):
        asyncio.run(music.play("spooky scary skeletons", target=self.BATH))
        self.assertEqual(self.queues.play_media.await_args.args[0], "ma_shower")

    def test_no_target_still_means_the_kitchen(self):
        """Every caller that predates rooms keeps its behaviour exactly."""
        asyncio.run(music.play("spooky scary skeletons"))
        self.assertEqual(self.queues.play_media.await_args.args[0],
                         config.MA_QUEUE_ID)

    # -- the amp and the volume -------------------------------------------
    def test_music_never_fires_an_amp_wake_of_its_own(self):
        """The obvious thing to do here is the wrong one. A wake tone is an MA
        announcement, and MA drops queue commands while one is in progress —
        so the tone ate the play_media a second behind it and the bath just
        stayed quiet ("Ignore queue command: An announcement is in progress",
        2026-08-09). The reply that follows the turn wakes the amp instead."""
        asyncio.run(music.play("spooky scary skeletons", target=self.BATH))
        self.wake.assert_not_awaited()

    def test_a_quiet_room_starts_at_its_configured_volume(self):
        asyncio.run(music.play("spooky scary skeletons", target=self.BATH))
        self.assertIn(("shower", 20), self._volumes())

    def test_a_room_already_playing_keeps_the_volume_it_was_turned_to(self):
        """Starting a second song must not undo a "turn it up" from a minute
        ago; only the first play into a quiet room asserts a level."""
        self._playing()
        asyncio.run(music.play("spooky scary skeletons", target=self.BATH))
        self.assertEqual(self._volumes(), [])

    def test_the_kitchen_volume_is_never_asserted(self):
        """Its levels are announcements + jukebox knob + New Mode, and voice
        music has no business overriding any of them."""
        asyncio.run(music.play("spooky scary skeletons", target=self.KITCHEN))
        self.assertEqual(self._volumes(), [])

    # -- side effects that belong to the kitchen ---------------------------
    def test_a_bath_play_leaves_the_nfc_jukebox_alone(self):
        """Clearing the card marker for a play two floors up would make the
        next scan of that card pause-toggle the bath instead of playing."""
        asyncio.run(music.play("spooky scary skeletons", target=self.BATH))
        music._notify_jukebox_takeover.assert_not_called()

    def test_a_kitchen_play_still_tells_the_jukebox(self):
        asyncio.run(music.play("spooky scary skeletons",
                               target={"queue": config.MA_QUEUE_ID, "local": True}))
        music._notify_jukebox_takeover.assert_called_once()

    # -- queue hygiene -----------------------------------------------------
    def test_repeat_and_dont_stop_the_music_are_cleared(self):
        """Both persist per queue and both would outlive the request. DSTM in
        particular refills the queue forever, which would defeat the cap."""
        asyncio.run(music.play("spooky scary skeletons", target=self.BATH))
        self.queues.queue_command_repeat.assert_awaited()
        self.queues.dont_stop_the_music.assert_awaited_with("ma_shower", False)

    # -- ducking -----------------------------------------------------------
    def test_ducking_reads_the_snapserver_not_music_assistant(self):
        """MA reported ma_shower at 0 while the room played at 20. Ducking
        from 0 computes a target of 5, sees 5 >= 0, and silently does nothing
        — the failure that makes this a room-by-room read rather than one."""
        self._playing()
        self.players.get.return_value = SimpleNamespace(volume_level=0)
        asyncio.run(music.duck(self.BATH))
        self.assertEqual(self._volumes(), [("shower", 5)])

    def test_two_rooms_duck_and_unduck_independently(self):
        """One global refcount meant the kitchen's unduck restored the bath's
        saved volume onto the kitchen."""
        self._playing()

        async def both():
            await music.duck(self.KITCHEN)      # MA says 60
            await music.duck(self.BATH)         # snapserver says 20
            await music.unduck(self.KITCHEN)

        asyncio.run(both())
        self.assertEqual(self._volumes(),
                         [("kitchen-box", 15), ("shower", 5),
                          ("kitchen-box", 60)])

    def test_a_second_duck_in_one_room_does_not_re_read_the_volume(self):
        """Refcounted: the alarm ducking on top of a turn must not capture the
        already-ducked level as the thing to restore."""
        self._playing()

        async def nested():
            await music.duck(self.BATH)
            await music.duck(self.BATH)
            await music.unduck(self.BATH)
            await music.unduck(self.BATH)

        asyncio.run(nested())
        self.assertEqual(self._volumes(), [("shower", 5), ("shower", 20)])

    # -- volume commands ---------------------------------------------------
    def test_turn_it_up_uses_the_real_volume(self):
        """From MA's stale 0 this would have SET the bath to 10 — a request to
        turn it up that halves it."""
        asyncio.run(music.control("volume_up", self.BATH))
        self.assertEqual(self._volumes(), [("shower", 30)])

    def test_turn_it_up_stops_below_alarm_volume(self):
        """Nobody should be able to talk a speaker into alarm territory with
        kids in the room under it."""
        self.snap.return_value = 35
        asyncio.run(music.control("volume_up", self.BATH))
        self.assertEqual(self._volumes(), [("shower", 40)])

    def test_turning_up_while_ducked_moves_the_restore_target(self):
        self._playing()

        async def ducked_then_up():
            await music.duck(self.BATH)
            await music.control("volume_up", self.BATH)
            await music.unduck(self.BATH)

        asyncio.run(ducked_then_up())
        self.assertEqual(self._volumes()[-1], ("shower", 30))

    def test_absolute_volume_while_ducked_replaces_the_restore_target(self):
        self._playing()
        kitchen = {"queue": config.MA_QUEUE_ID, "local": True}

        async def ducked_then_set():
            with patch.object(music, "_notify_jukebox_volume_hold",
                              new=AsyncMock()) as hold:
                await music.duck(kitchen)
                effective = await music.control("volume_set", kitchen, 80)
                await music.unduck(kitchen)
                return effective, hold.await_args.args[0]

        effective, held = asyncio.run(ducked_then_set())
        self.assertEqual(effective, 80)
        self.assertEqual(held, 80)
        self.assertEqual(self._volumes()[-1], (config.MA_QUEUE_ID, 80))

    def test_absolute_volume_obeys_a_rooms_safety_ceiling(self):
        effective = asyncio.run(music.control("volume_set", self.BATH, 80))
        self.assertEqual(effective, 40)
        self.assertEqual(self._volumes(), [("shower", 40)])

    def test_normal_volume_clears_hold_without_bypassing_duck(self):
        self._playing()
        kitchen = {"queue": config.MA_QUEUE_ID, "local": True}

        async def ducked_then_normal():
            with patch.object(music, "_clear_jukebox_volume_hold",
                              new=AsyncMock(return_value=55)):
                await music.duck(kitchen)
                effective = await music.control("volume_normal", kitchen)
                await music.unduck(kitchen)
                return effective

        effective = asyncio.run(ducked_then_normal())
        self.assertEqual(effective, 55)
        self.assertEqual(self._volumes()[-1], (config.MA_QUEUE_ID, 55))

    # -- the auto-stop cap -------------------------------------------------
    def test_a_capped_room_arms_a_stop(self):
        """The people it is playing for are in the tub and cannot reach a
        microphone, so something other than them has to end it."""
        asyncio.run(music.play("spooky scary skeletons", target=self.BATH))
        self.assertIn("ma_shower", music._caps)

    def test_the_kitchen_is_not_capped(self):
        asyncio.run(music.play("spooky scary skeletons", target=self.KITCHEN))
        self.assertEqual(music._caps, {})

    def test_stopping_disarms_the_cap(self):
        async def play_then_stop():
            await music.play("spooky scary skeletons", target=self.BATH)
            await music.control("stop", self.BATH)

        asyncio.run(play_then_stop())
        self.assertEqual(music._caps, {})

    def test_the_cap_stops_the_room(self):
        async def expire():
            with patch.object(music.asyncio, "sleep", new=AsyncMock()):
                await music._cap_expire("ma_shower", 60)

        asyncio.run(expire())
        self.queues.queue_command_stop.assert_awaited_with("ma_shower")


if __name__ == "__main__":
    unittest.main()
