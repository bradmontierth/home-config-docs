import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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


if __name__ == "__main__":
    unittest.main()
