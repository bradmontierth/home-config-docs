"""Music via Music Assistant ("okay computer, play Raffi").

One persistent MusicAssistantClient over MA's WebSocket JSON-RPC (there is no
REST search), reconnecting forever in the background — MA being down must never
break timers/lists/ask. Every public helper raises MusicUnavailable when the
connection isn't up; callers turn that into a spoken "can't reach the music
player".

Target is always the kitchen jukebox queue (MA_QUEUE_ID) — the same
squeezelite player the NFC reader drives, so voice and cards coexist.

Ranking (see plan doc, revised 2026-07-08): a playlist wins only on a
strong/near-exact name match; a bare artist name plays the library artist,
shuffled (fresh every time, beats freezing onto one stale playlist); then
album, then track. Within a bucket prefer `library://` URIs — playing a
library item lets MA pick the stream provider PER TRACK, so local lossless
beats Spotify automatically. Low confidence still plays the best guess: nobody
wants a "did you mean" dialog mid-cooking.

Ducking: the satellite POSTs /music/duck on a stage-1 wake trigger and on
alarm start (music at speech volume defeats verify/capture), /music/unduck
when the turn or alarm ends. Nested duck/unduck pairs are refcounted; a
watchdog restores the volume anyway if the satellite dies mid-turn and the
unduck never arrives.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

import aiohttp
from music_assistant_client.client import MusicAssistantClient
from music_assistant_models.enums import MediaType, PlayerState, QueueOption
from rapidfuzz import fuzz

from . import config

log = logging.getLogger("orchestrator.music")


class MusicUnavailable(RuntimeError):
    """Music Assistant is not connected right now."""


# The 2.6.x server rejects the client's default media-type list (it includes
# GENRE, which this schema doesn't implement) — always pass these explicitly.
SEARCH_TYPES = [MediaType.PLAYLIST, MediaType.ARTIST, MediaType.ALBUM, MediaType.TRACK]

_client: MusicAssistantClient | None = None
_runner: asyncio.Task | None = None


def start() -> None:
    global _runner
    _runner = asyncio.create_task(_run(), name="ma-connection")


async def stop() -> None:
    if _runner:
        _runner.cancel()
        try:
            await _runner
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


async def _run() -> None:
    """Own the MA connection for the process lifetime; reconnect on any drop."""
    global _client
    while True:
        session = aiohttp.ClientSession()
        try:
            client = MusicAssistantClient(config.MA_URL, session)
            await client.connect()
            ready = asyncio.Event()
            listen = asyncio.create_task(client.start_listening(ready))
            await ready.wait()
            _client = client
            log.info("Music Assistant connected (%s, server %s)",
                     config.MA_URL, client.server_info.server_version)
            asyncio.create_task(_refresh_index_guarded())   # warm the resolver
            await listen          # returns/raises only when the connection dies
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("Music Assistant connection lost: %s", exc)
        finally:
            _client = None
            await session.close()
        await asyncio.sleep(5)


def _ma() -> MusicAssistantClient:
    if _client is None:
        raise MusicUnavailable("Music Assistant is not connected")
    return _client


# --------------------------------------------------------------------------
# library index — the ASR-robust resolver (checked BEFORE MA search)
# --------------------------------------------------------------------------
# MA's search is literal: for a Parakeet misspelling ("rafi", "raffie") the
# library provider returns NOTHING while Spotify returns real junk artists
# with those exact names — so search-based ranking can never recover the
# household's own music from a mangled transcript. Instead, keep every
# library name in memory and fuzzy-match the query against it first; only
# fall through to MA search when nothing in the library is close.
_index: dict = {"ts": 0.0, "entries": [], "refreshing": None}
_PAGE = 500   # get_library_* silently cap an unlimited call at a server page


async def _refresh_index() -> None:
    client = _ma()
    m = client.music
    entries: list[dict] = []
    for kind, fetch in (("playlist", m.get_library_playlists),
                        ("artist", m.get_library_artists),
                        ("album", m.get_library_albums),
                        ("track", m.get_library_tracks)):
        offset = 0
        while True:
            page = await fetch(limit=_PAGE, offset=offset)
            for it in page:
                norm = _norm(it.name)
                if not norm:
                    continue
                arts = getattr(it, "artists", None) or []
                entries.append({
                    "kind": kind, "name": it.name, "uri": it.uri,
                    "norm": norm, "collapsed": _collapse(norm),
                    "artist": arts[0].name if arts else None,
                    "local": _has_local(it),
                })
            if len(page) < _PAGE:
                break
            offset += _PAGE
    _index["entries"] = entries
    _index["ts"] = time.time()
    log.info("library index refreshed: %d entries", len(entries))


async def _refresh_index_guarded() -> None:
    try:
        await _refresh_index()
    except Exception as exc:  # noqa: BLE001
        log.warning("library index refresh failed: %s", exc)


async def _ensure_index() -> list[dict]:
    """Current index; a stale one is served as-is while a background refresh
    runs (a play command must never wait ~1s on a full library fetch)."""
    age = time.time() - _index["ts"]
    if _index["entries"] and age < config.MUSIC_INDEX_TTL_S:
        return _index["entries"]
    if _index["entries"]:
        if _index["refreshing"] is None or _index["refreshing"].done():
            _index["refreshing"] = asyncio.create_task(_refresh_index_guarded())
        return _index["entries"]
    await _refresh_index()
    return _index["entries"]


def _collapse(s: str) -> str:
    """Squash doubled letters — poor-man's phonetics for exactly the thing ASR
    can't hear ("raffi"/"rafi", "will.i.am" aside)."""
    return re.sub(r"(.)\1+", r"\1", s)


def _entry_score(e: dict, qn: str, qc: str, qtokens: list[str]) -> float:
    score = max(fuzz.ratio(qn, e["norm"]), fuzz.ratio(qc, e["collapsed"]))
    # A garbled multiword transcript ("lenny rafi") can still contain a
    # single-word ARTIST name — score each query token too, but only accept a
    # near-exact token hit (≥90): this path is for recovering a name buried in
    # debris, and at lower bars it invents matches ("spears" ≈ track "Sparks"
    # at 83 hijacked a Britney request from the search fallthrough).
    if e["kind"] == "artist" and " " not in e["norm"] and len(e["norm"]) >= 4:
        for tok in qtokens:
            if len(tok) >= 4:
                ts = fuzz.ratio(_collapse(tok), e["collapsed"])
                if ts >= 90:
                    score = max(score, ts)
    return score


async def _resolve_library(query: str, media_type: str | None) -> dict | None:
    """Best library match for a (possibly misspelled) query, or None. Same
    bucket precedence + thresholds as the search ranker."""
    entries = await _ensure_index()
    qn = _norm(query)
    if not entries or not qn:
        return None
    # "baby beluga by raffi" — the by-tail wrecks a whole-string ratio against
    # the track name; score with and without it.
    qn2 = re.sub(r"\bby [a-z0-9 ]+$", "", qn).strip()
    variants = [(qn, _collapse(qn), qn.split())]
    if qn2 and qn2 != qn:
        variants.append((qn2, _collapse(qn2), qn2.split()))
    best: dict[str, tuple[float, dict]] = {}
    for e in entries:
        s = max(_entry_score(e, *v) for v in variants)
        cur = best.get(e["kind"])
        # Ties go to locally-mapped items: of two library albums named "Baby
        # Beluga", play the one whose files we own.
        if cur is None or (s, e["local"]) > (cur[0], cur[1]["local"]):
            best[e["kind"]] = (s, e)
    if media_type in _RESULT_ATTR:          # user literally named the type
        hit = best.get(media_type)
        if hit and hit[0] >= 60:
            return {**hit[1], "score": hit[0]}
        return None
    for kind, threshold in _BUCKETS:
        hit = best.get(kind)
        if hit and hit[0] >= threshold:
            return {**hit[1], "score": hit[0]}
    return None


# --------------------------------------------------------------------------
# search + ranking
# --------------------------------------------------------------------------
def _norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    s = re.sub(r"\bthe\b", " ", s)
    return " ".join(s.split())


def _name_score(query: str, name: str) -> float:
    return fuzz.token_sort_ratio(_norm(query), _norm(name))


def _is_library(item) -> bool:
    return (item.uri or "").startswith("library://")


def _has_local(item) -> bool:
    return any(m.provider_domain == "filesystem_local"
               for m in (item.provider_mappings or []))


def _best(query: str, items) -> tuple[float, object] | None:
    """Best fuzzy match in a bucket; library items win ties (then local-mapped
    ones — of two library albums with the same name, prefer the owned files)."""
    if not items:
        return None
    ranked = max(items, key=lambda it: (_name_score(query, it.name),
                                        _is_library(it), _has_local(it)))
    return _name_score(query, ranked.name), ranked


# Bucket order + how close the name must be for that bucket to claim the query.
# Playlist demands near-exact ("the best of raffi" -> playlist; bare "raffi"
# scores ~55 against it and falls through to the artist).
_BUCKETS = (("playlist", 92), ("artist", 80), ("album", 85), ("track", 80))
_RESULT_ATTR = {"playlist": "playlists", "artist": "artists",
                "album": "albums", "track": "tracks"}


def _rank(query: str, results, media_type: str | None) -> tuple[object, str] | None:
    """Pick what to play: (media item, kind). None only if MA returned nothing."""
    if media_type in _RESULT_ATTR:
        best = _best(query, getattr(results, _RESULT_ATTR[media_type]))
        if best and best[0] >= 60:      # they NAMED the type — honor it readily
            return best[1], media_type
    # Two passes: library-only candidates get first claim in every bucket, THEN
    # anything goes. Spotify search is full of traps for the general pass — a
    # random user playlist or a junk "artist" literally named after a song
    # ("Wheels on the Bus") can name-match 100 and steal a query the household's
    # own library should win ("play baby beluga" must hit the owned album).
    for library_only in (True, False):
        for kind, threshold in _BUCKETS:
            items = getattr(results, _RESULT_ATTR[kind])
            if library_only:
                items = [it for it in items if _is_library(it)]
            best = _best(query, items)
            if best and best[0] >= threshold:
                return best[1], kind
    # Nothing confident — play the best guess anyway (no mid-cooking dialogs).
    overall = None
    for kind, _ in _BUCKETS:
        best = _best(query, getattr(results, _RESULT_ATTR[kind]))
        if best and (overall is None or best[0] > overall[0]):
            overall = (best[0], best[1], kind)
    if overall is None:
        return None
    return overall[1], overall[2]


def _artist_of(item) -> str | None:
    artists = getattr(item, "artists", None) or []
    return artists[0].name if artists else None


async def play(query: str | None, media_type: str | None = None) -> dict:
    """Start playback on the kitchen queue. No query ("play some music") just
    resumes whatever the queue holds. Returns a dict for phrasing/events."""
    client = _ma()
    qid = config.MA_QUEUE_ID
    if not query:
        await client.player_queues.queue_command_resume(qid)
        return {"kind": "resume", "name": None}
    # Library index first (survives ASR misspellings); MA search only when the
    # library has nothing close.
    sel = None
    via = "library-index"
    try:
        sel = await _resolve_library(query, media_type)
    except Exception as exc:  # noqa: BLE001 — resolver trouble must not kill play
        log.warning("library resolver failed, falling back to search: %s", exc)
    if sel is None:
        via = "search"
        results = await client.music.search(query, media_types=SEARCH_TYPES, limit=10)
        pick = _rank(query, results, media_type)
        if pick is None:
            raise LookupError(f"no results for {query!r}")
        item, kind = pick
        sel = {"kind": kind, "name": item.name, "uri": item.uri,
               "artist": _artist_of(item)}
    # Artist/playlist get shuffle (fresh mix each time); album/track play in order.
    shuffle = sel["kind"] in ("artist", "playlist")
    await client.player_queues.queue_command_shuffle(qid, shuffle)
    await client.player_queues.play_media(qid, sel["uri"], option=QueueOption.REPLACE)
    log.info("play_music %r -> %s %r (%s, shuffle=%s, via=%s, score=%s)",
             query, sel["kind"], sel["name"], sel["uri"], shuffle, via,
             sel.get("score"))
    return {"kind": sel["kind"], "name": sel["name"], "artist": sel.get("artist"),
            "uri": sel["uri"], "shuffle": shuffle}


# --------------------------------------------------------------------------
# transport + now playing
# --------------------------------------------------------------------------
async def control(action: str) -> None:
    client = _ma()
    qid = config.MA_QUEUE_ID
    if action == "pause":
        await client.player_queues.queue_command_pause(qid)
    elif action == "resume":
        await client.player_queues.queue_command_resume(qid)
    elif action == "stop":
        await client.player_queues.queue_command_stop(qid)
    elif action == "next":
        await client.player_queues.queue_command_next(qid)
    elif action == "previous":
        await client.player_queues.queue_command_previous(qid)
    elif action in ("volume_up", "volume_down"):
        step = 10 if action == "volume_up" else -10
        async with _duck_lock:
            if _duck["count"] and _duck["restore"] is not None:
                # Music is ducked for this very turn — changing the live volume
                # would be wiped by the unduck. Adjust the restore target instead.
                _duck["restore"] = max(0, min(100, _duck["restore"] + step))
                return
        player = client.players.get(qid)
        cur = (player.volume_level or 0) if player else 0
        await client.players.player_command_volume_set(
            qid, max(0, min(100, cur + step)))
    else:
        raise ValueError(f"unknown music action {action!r}")


def now_playing() -> dict | None:
    """Current kitchen-queue item from client cache, or None when idle."""
    client = _ma()
    queue = client.player_queues.get(config.MA_QUEUE_ID)
    if queue is None or queue.state not in (PlayerState.PLAYING, PlayerState.PAUSED):
        return None
    cur = queue.current_item
    if cur is None:
        return None
    media = cur.media_item
    track = getattr(media, "name", None) or cur.name
    return {
        "state": queue.state.value,
        "track": track,
        "artist": _artist_of(media) if media else None,
        "album": getattr(getattr(media, "album", None), "name", None),
    }


# --------------------------------------------------------------------------
# ducking (wake turns + alarms)
# --------------------------------------------------------------------------
_duck: dict = {"count": 0, "restore": None}
_duck_lock = asyncio.Lock()
_duck_watchdog: asyncio.Task | None = None


async def duck() -> None:
    """Drop the music volume so verify/capture (and the alarm 'stop' listener)
    can hear speech. Refcounted — a turn and an alarm may overlap."""
    global _duck_watchdog
    client = _ma()
    qid = config.MA_QUEUE_ID
    async with _duck_lock:
        _duck["count"] += 1
        if _duck["count"] > 1:
            return
        queue = client.player_queues.get(qid)
        player = client.players.get(qid)
        if not player or not queue or queue.state != PlayerState.PLAYING:
            _duck["restore"] = None      # nothing audible — duck is a no-op
            return
        vol = player.volume_level or 0
        target = max(config.MUSIC_DUCK_MIN, round(vol * config.MUSIC_DUCK_FACTOR))
        if target >= vol:
            _duck["restore"] = None
            return
        _duck["restore"] = vol
        await client.players.player_command_volume_set(qid, target)
        # If the satellite dies mid-turn the unduck never arrives — restore
        # anyway after the longest plausible turn+alarm.
        if _duck_watchdog:
            _duck_watchdog.cancel()
        _duck_watchdog = asyncio.create_task(_duck_expire())
        log.info("music ducked %d -> %d", vol, target)


async def unduck() -> None:
    global _duck_watchdog
    async with _duck_lock:
        if _duck["count"] == 0:
            return
        _duck["count"] -= 1
        if _duck["count"]:
            return
        if _duck_watchdog:
            _duck_watchdog.cancel()
            _duck_watchdog = None
        restore, _duck["restore"] = _duck["restore"], None
    if restore is not None:
        await _ma().players.player_command_volume_set(config.MA_QUEUE_ID, restore)
        log.info("music unducked -> %d", restore)


async def _duck_expire() -> None:
    global _duck_watchdog
    await asyncio.sleep(config.MUSIC_DUCK_TTL_S)
    log.warning("duck watchdog fired — unduck never arrived; restoring volume")
    # Detach ourselves FIRST: unduck() cancels _duck_watchdog, and that's this
    # very task — cancelling ourselves mid-unduck would skip the restore.
    _duck_watchdog = None
    async with _duck_lock:
        _duck["count"] = 1               # force this unduck to restore
    await unduck()
