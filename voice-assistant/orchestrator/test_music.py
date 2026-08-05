import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from . import music


def _entry(kind: str, name: str, artist: str | None, local: bool, uri: str,
           version: str = "") -> dict:
    """One library-index row, built exactly the way _refresh_index does."""
    norm = music._norm(name)
    titled = f"{name} {version}" if version else name
    full = music._norm(f"{artist} {titled}") if artist else music._norm(titled)
    return {"kind": kind, "name": name, "uri": uri,
            "norm": norm, "collapsed": music._collapse(norm),
            "skel": music._skeleton(norm),
            "full": full, "fullc": music._collapse(full),
            "ftoks": frozenset(full.split()),
            "artist": artist, "local": local}


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
]


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
                            exclude={"library://track/2431",
                                     "library://track/2432"})
        self.assertEqual(hit["uri"], "library://album/104")

    def test_exclude_can_empty_the_field(self):
        hit = self._resolve("spooky scary skeletons",
                            exclude={"library://track/2431",
                                     "library://track/2432",
                                     "library://album/104"})
        self.assertIsNone(hit)


class PlayFallbackTest(unittest.TestCase):
    """play() re-ranks when MA refuses the URI the resolver picked."""

    def setUp(self):
        self.queues = AsyncMock()
        client = AsyncMock()
        client.player_queues = self.queues
        patcher = patch.object(music, "_ma", return_value=client)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(music, "_ensure_index",
                               new=AsyncMock(return_value=SKELETONS))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(music, "_notify_jukebox_takeover", new=AsyncMock())
        patcher.start()
        self.addCleanup(patcher.stop)

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
