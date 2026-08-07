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

Ducking: the satellite POSTs /music/duck on a CONFIRMED wake (stage-2 verify
pass, not stage-1 triggers — music false-fires made the dips audible as
playback stutter) and on alarm start (music at speech volume defeats command
capture), /music/unduck when the turn or alarm ends. Nested duck/unduck pairs are refcounted; a
watchdog restores the volume anyway if the satellite dies mid-turn and the
unduck never arrives.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata

import aiohttp
from music_assistant_client.client import MusicAssistantClient
from music_assistant_models.enums import MediaType, PlayerState, QueueOption
from rapidfuzz import fuzz

from . import config, music_log

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
                artist = arts[0].name if arts else None
                # MA keeps the variant OUT of the name ("Spooky, Scary
                # Skeletons" + version "Undead Tombstone Remix"), so two
                # library tracks can be byte-identical by name and a spoken
                # "remix"/"live"/"acoustic" has nothing to bite on. Fold the
                # version into the token-set field only — norm/skeleton stay
                # the bare name, so unqualified queries score as before.
                version = (getattr(it, "version", None) or "").strip()
                titled = f"{it.name} {version}" if version else it.name
                # artist+name in one string lets token-set scoring absorb a
                # spoken composer/artist qualifier ("Chopin's ballade 4",
                # "baby beluga by raffi") without stripping heuristics.
                full = _norm(f"{artist} {titled}") if artist else _norm(titled)
                entries.append({
                    "kind": kind, "name": it.name, "uri": it.uri,
                    "norm": norm, "collapsed": _collapse(norm),
                    "skel": _skeleton(norm),
                    "full": full, "fullc": _collapse(full),
                    "ftoks": frozenset(full.split()),
                    "artist": artist,
                    "local": _has_local(it),
                    "owned": _is_owned(it),
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


def _skeleton(s: str) -> str:
    """Sound-spelling skeleton: spaces out, vowel RUNS (y and h count as
    vowel-ish) collapsed to one marker, doubled consonants squashed. Bridges
    the ASR class where the SOUND matched but the spelling didn't — Parakeet
    wrote "Deo" for the track "Day O" (both -> "dV"), and "day oh" lands there
    too (h rides the vowel run). Deliberately NOT metaphone: on names this
    short a consonant key like "T" matches half the library, and metaphone
    encodes "deo"/"day o" differently anyway. Skeletons are compared for
    EXACT equality only (they're too short for fuzzy), and only within the
    library index — worst case is the wrong OWNED song, never Spotify junk."""
    s = s.replace(" ", "")
    s = re.sub(r"[aeiouyh]+", "V", s)
    return re.sub(r"(.)\1+", r"\1", s)


def _entry_score(e: dict, qn: str, qc: str, qtokens: list[str], qs: str) -> float:
    score = max(fuzz.ratio(qn, e["norm"]), fuzz.ratio(qc, e["collapsed"]))
    # Phonetic-skeleton equality ("deo" == "day o" == "dV"): strong but not
    # supreme — 90.x clears the artist/album/track bars yet stays below the
    # playlist bar (92, near-exact by design) and below any true lexical ~100
    # competitor. The plain ratio rides along as a tiebreak so the lexically
    # closest of several skeleton-equal names wins. len>=2 guards degenerate
    # skeletons ("V") from matching everything.
    if len(qs) >= 2 and qs == e["skel"]:
        score = max(score, 90.0 + fuzz.ratio(qn, e["norm"]) / 100.0)
    # Tracks/albums: formal names carry tails the user never says ("Ballade
    # No. 4 IN F MINOR, OP. 52") and queries carry an artist the name doesn't
    # ("CHOPIN'S ballade 4"). token_set_ratio scores on the exact-token
    # intersection vs each side's leftovers, and matching against artist+name
    # ("full") absorbs the spoken qualifier — "chopin s balade 4" ∩
    # "frederic chopin balade 4 in f minor op 52" ≈ 93. Exact-token
    # intersection is what keeps this safe: "spears" never intersects
    # "sparks". Multiword queries only — a single token would "fully match"
    # anything containing it.
    if e["kind"] in ("track", "album") and len(qtokens) >= 2:
        ts = max(fuzz.token_set_ratio(qn, e["full"]),
                 fuzz.token_set_ratio(qc, e["fullc"]))
        # Weight by how much of the NAME the query explains, so "the fourth
        # ballade" prefers the track "Ballade No. 4 in F minor" over a
        # compilation album whose title happens to list its contents
        # ("...Piano Concerto No.2 - Ballade No.4 - Berceuse..."). A perfect
        # full-name match keeps 100; sprawling titles bleed a few points.
        cov = len(e["ftoks"].intersection(qtokens)) / (len(e["ftoks"]) or 1)
        # +1 track nudge, subset path only: a query that's a fragment of a
        # longer title names a PIECE, and compilation-album titles that list
        # their contents ("...Ballade No.4 - Berceuse...") otherwise tie the
        # real track exactly. Full-name matches score via plain ratio above,
        # so "baby beluga" still ties album-vs-track and keeps the album.
        bonus = 1.0 if e["kind"] == "track" else 0.0
        score = max(score, ts * (0.85 + 0.15 * cov) + bonus)
    # A garbled SHORT transcript ("lenny rafi") can still contain a
    # single-word ARTIST name — score each query token too, but only accept a
    # near-exact token hit (≥90): this path is for recovering a name buried in
    # debris, and at lower bars it invents matches ("spears" ≈ track "Sparks"
    # at 83 hijacked a Britney request from the search fallthrough). ≤3 tokens
    # only: a longer query names a PIECE ("Chopin's ballade number four") and
    # the bare composer token must not steal it from the track bucket.
    if (e["kind"] == "artist" and len(qtokens) <= 3
            and " " not in e["norm"] and len(e["norm"]) >= 4):
        for tok in qtokens:
            if len(tok) >= 4:
                ts = fuzz.ratio(_collapse(tok), e["collapsed"])
                if ts >= 90:
                    score = max(score, ts)
    return score


# Album/track dropped from 85/80 to 70 on 2026-08-06. The high bars were
# calibrated against an asymmetric downside that no longer exists: a
# below-threshold library match used to fall through to MA search, where the
# prize for a near-miss was a stranger's recording — or Spotify junk — in front
# of a kid. Spotify is gone and MUSIC_OWNED_ONLY refuses rather than searches,
# so the worst case now is the wrong song out of our OWN library, which is a
# shrug and a second command. Playlist (92, near-exact by design) and artist
# (80) deliberately did NOT move: both are checked BEFORE album/track, so a
# loosened bar there doesn't recover misses, it STEALS them — a weak artist
# match turns a request for one piece into a shuffle of everything ("spears"
# scored 83 against the track "Sparks" once already).
_LIB_THRESHOLDS = {"playlist": 92, "artist": 80, "album": 70, "track": 70}
# Play attempts (winner + fallbacks) before a play_media failure reaches the
# user. Kept small: each one is a real round-trip and the kitchen is waiting.
_PLAY_ATTEMPTS = 3


async def _resolve_library(query: str, media_type: str | None,
                           relaxed: bool = False,
                           exclude: set[str] | None = None,
                           trace: dict | None = None) -> dict | None:
    """Best library match for a (possibly misspelled) query, or None. Same
    bucket precedence + thresholds as the search ranker.

    relaxed=True is the LAST-RESORT pass play() runs only after MA search also
    failed (its winner was a below-threshold guess): a merely-plausible OWNED
    match beats a scoreless Spotify roll of the dice ("Deo" once drew spotify
    track 'demons'). Relaxed mode ignores bucket precedence — with low floors,
    a weak early bucket would steal from a strong later one (a 57-scoring
    artist beat the 90-scoring Day O track in testing) — and just takes the
    best score anywhere at a flat 60 floor.

    exclude drops URIs a previous play attempt already proved unplayable, so
    play() can re-run the whole ranking for the next-best candidate.

    trace, if given, is filled with the per-bucket ranking (scores and the bar
    each one had to clear) even when the answer is None — a refusal's near-miss
    is the whole point of music_log."""
    entries = await _ensure_index()
    qn = _norm(query)
    if not entries or not qn:
        return None
    # "baby beluga by raffi" — the by-tail wrecks a whole-string ratio against
    # the track name; score with and without it.
    qn2 = re.sub(r"\bby ([a-z0-9 ]+)$", "", qn).strip()
    tail_artist = (m.group(1).strip() if (m := re.search(r"\bby ([a-z0-9 ]+)$", qn))
                   else None)
    # ...and once more with the connector word itself dropped, artist kept.
    # "by" is a query-only token — no title has anything for it to intersect —
    # so token_set_ratio pays full freight for it: "jupiter by holst" scores
    # 89.7 against "gustav holst ... jupiter ..." where "jupiter holst" scores
    # 100, and the coverage weight turns that 10-point drag into the whole
    # difference between 79.65 and a bar of 80. The stripped-tail variant above
    # can't cover this, because what it leaves ("jupiter") is a single token and
    # the token-set path needs two. Safe by construction: variants are combined
    # with max(), so a new one can only RAISE a score — "Stand By Me" still
    # matches on the original.
    qn3 = " ".join(t for t in qn.split() if t != "by")
    variants = [(qn, _collapse(qn), qn.split(), _skeleton(qn))]
    for v in (qn2, qn3):
        if v and v != qn and all(v != x[0] for x in variants):
            variants.append((v, _collapse(v), v.split(), _skeleton(v)))
    best: dict[str, tuple[float, dict]] = {}
    for e in entries:
        if exclude and e["uri"] in exclude:
            continue
        if config.MUSIC_OWNED_ONLY and not e.get("owned", True):
            continue
        s = max(_entry_score(e, *v) for v in variants)
        cur = best.get(e["kind"])
        # Ties go to locally-mapped items: of two library albums named "Baby
        # Beluga", play the one whose files we own.
        if cur is None or (s, e["local"]) > (cur[0], cur[1]["local"]):
            best[e["kind"]] = (s, e)
    if trace is not None:
        trace["candidates"] = sorted(
            [{"kind": k, "name": e["name"], "artist": e["artist"],
              "uri": e["uri"], "score": round(s, 2), "bar": _LIB_THRESHOLDS[k]}
             for k, (s, e) in best.items()],
            key=lambda c: c["score"], reverse=True)
    if relaxed:
        hits = [h for h in best.values() if h[0] >= 60]
        if not hits:
            return None
        hit = max(hits, key=lambda h: (h[0], h[1]["local"]))
        return {**hit[1], "score": hit[0]}
    if media_type in _RESULT_ATTR:          # user literally named the type
        hit = best.get(media_type)
        if hit and hit[0] >= 60:
            return {**hit[1], "score": hit[0]}
        return None
    # "X by Y": if Y names the artist of a strong track/album hit, the user
    # asked for that PIECE — don't let the artist bucket steal it into a
    # shuffle ("deo by rafi" must play Day O, not shuffle Raffi).
    if tail_artist:
        piece = [hit for kind in ("album", "track")
                 if (hit := best.get(kind)) and hit[0] >= _LIB_THRESHOLDS[kind]
                 and hit[1].get("artist")
                 and fuzz.ratio(_collapse(tail_artist),
                                _collapse(_norm(hit[1]["artist"]))) >= 80]
        if piece:
            hit = max(piece, key=lambda h: (h[0], h[1]["local"]))
            return {**hit[1], "score": hit[0]}
    for kind in ("playlist", "artist"):
        hit = best.get(kind)
        if hit and hit[0] >= _LIB_THRESHOLDS[kind]:
            return {**hit[1], "score": hit[0]}
    # Album vs track compete on SCORE (ties → the one whose files we OWN):
    # strict bucket order let any qualifying album beat a better-matching track
    # ("the fourth ballade" lost to a concert-program album title that merely
    # mentions Ballade 4). The local tiebreak matters because a Spotify-only
    # entry is a coin flip on MA's provider being alive — a favorited Spotify
    # single "Spooky, Scary Skeletons" tied the owned Andrew Gold track at 88
    # and won on list order, then failed to play at all (MA's Spotify provider
    # had been dead since a failed token refresh on its last restart).
    contenders = [hit for kind in ("album", "track")
                  if (hit := best.get(kind)) and hit[0] >= _LIB_THRESHOLDS[kind]]
    if contenders:
        hit = max(contenders, key=lambda h: (h[0], h[1]["local"]))
        return {**hit[1], "score": hit[0]}
    return None


# --------------------------------------------------------------------------
# search + ranking
# --------------------------------------------------------------------------
# Spoken numbers vs printed track names ("ballade number four" vs "Ballade
# No. 4"): normalize both sides to bare digits so edit distance never has to
# bridge "four"↔"4". Applied inside _norm, so titles containing number WORDS
# normalize identically on the index and the query ("One Light, One Sun").
_NUM_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18",
    "nineteen": "19", "twenty": "20",
    "first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5",
    "sixth": "6", "seventh": "7", "eighth": "8", "ninth": "9", "tenth": "10",
    "eleventh": "11", "twelfth": "12", "thirteenth": "13", "fourteenth": "14",
    "fifteenth": "15", "sixteenth": "16", "seventeenth": "17",
    "eighteenth": "18", "nineteenth": "19", "twentieth": "20",
    "opus": "op",
}


def _norm(s: str) -> str:
    # Fold accents BEFORE the ascii strip: "Frédéric" must become "frederic",
    # not "fr d ric" (three junk tokens that wreck token-set coverage).
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    s = re.sub(r"\bthe\b", " ", s)
    s = " ".join(_NUM_WORDS.get(t, t) for t in s.split())
    # "no 4" / "number 4" / "num 4" -> "4" ("#4" already lost its # above)
    s = re.sub(r"\b(?:no|number|num)\s+(\d+)\b", r"\1", s)
    return " ".join(s.split())


def _name_score(query: str, name: str) -> float:
    return fuzz.token_sort_ratio(_norm(query), _norm(name))


def _is_library(item) -> bool:
    return (item.uri or "").startswith("library://")


def _has_local(item) -> bool:
    return any(m.provider_domain == "filesystem_local"
               for m in (item.provider_mappings or []))


# Providers whose content is ours. "builtin" earns its place because its
# playlists ("500 Random tracks", "Random Album") are drawn from the library
# itself — no local file mapping of their own, but nothing foreign either.
_OWNED_PROVIDERS = frozenset({"filesystem_local", "builtin"})


def _is_owned(item) -> bool:
    return any(m.provider_domain in _OWNED_PROVIDERS
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


def _rank(query: str, results, media_type: str | None) -> tuple[object, str, bool] | None:
    """Pick what to play: (media item, kind, confident). None only if MA
    returned nothing. confident=False means the pick is a below-threshold
    best guess — play() gives the library one relaxed-floor look first."""
    if media_type in _RESULT_ATTR:
        best = _best(query, getattr(results, _RESULT_ATTR[media_type]))
        if best and best[0] >= 60:      # they NAMED the type — honor it readily
            return best[1], media_type, True
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
                return best[1], kind, True
    # Nothing confident — play the best guess anyway (no mid-cooking dialogs).
    overall = None
    for kind, _ in _BUCKETS:
        best = _best(query, getattr(results, _RESULT_ATTR[kind]))
        if best and (overall is None or best[0] > overall[0]):
            overall = (best[0], best[1], kind)
    if overall is None:
        return None
    return overall[1], overall[2], False


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
    trace: dict = {}
    try:
        sel = await _resolve_library(query, media_type, trace=trace)
    except Exception as exc:  # noqa: BLE001 — resolver trouble must not kill play
        log.warning("library resolver failed, falling back to search: %s", exc)
    if sel is None and config.MUSIC_OWNED_ONLY:
        # Nothing we own is close enough. Say so — the online search that used
        # to run here is exactly the path that answers a kid's request for a
        # song we don't have with a stranger's recording of the same title.
        # Keep the ranking: how far under the bar our refusals land is the
        # only evidence that can tell a too-high bar from an absent song.
        near = (trace.get("candidates") or [{}])[0]
        log.info("play_music %r -> no owned match (best %s %r score=%s bar=%s)",
                 query, near.get("kind"), near.get("name"), near.get("score"),
                 near.get("bar"))
        music_log.record(query, "refuse", winner=near or None,
                         candidates=trace.get("candidates"))
        raise LookupError(f"no owned match for {query!r}")
    if sel is None:
        via = "search"
        try:
            results = await client.music.search(query, media_types=SEARCH_TYPES,
                                                limit=10)
        except Exception as exc:  # noqa: BLE001
            # MA search dies whole even when only ONE provider is sick — its
            # recurring Spotify token-refresh bug (KeyError 'refresh_token')
            # was killing plays of music we own. Same relaxed-floor gamble as
            # below: most plays are library music, so a plausible owned match
            # beats refusing to play anything.
            log.warning("MA search failed (%s) — trying relaxed library match", exc)
            try:
                sel = await _resolve_library(query, media_type, relaxed=True)
            except Exception:  # noqa: BLE001 — fallback is best-effort
                sel = None
            if sel is None:
                raise
            via = "library-relaxed-searchdown"
    if sel is None:
        pick = _rank(query, results, media_type)
        if pick is None:
            raise LookupError(f"no results for {query!r}")
        item, kind, confident = pick
        if not confident:
            # Search's winner is a scoreless guess (random Spotify anything).
            # A merely-plausible OWNED match is the better gamble — "Deo"
            # (ASR for "Day O") once drew spotify track 'demons' here.
            try:
                relaxed = await _resolve_library(query, media_type, relaxed=True)
            except Exception:  # noqa: BLE001 — guard is best-effort
                relaxed = None
            if relaxed:
                sel, via = relaxed, "library-relaxed"
        if sel is None:
            sel = {"kind": kind, "name": item.name, "uri": item.uri,
                   "artist": _artist_of(item)}
    # A resolved winner is not a PLAYABLE winner: MA expands the URI at play
    # time and can come back empty (a favorited Spotify single whose provider
    # is down has no tracks to fetch — "No playable items found"). Treat that
    # as a rejection of that URI, not of the request, and re-rank without it:
    # the runner-up is usually the same song from the library.
    tried: set[str] = set()
    while True:
        # Artist/playlist get shuffle (fresh mix each time); album/track in order.
        shuffle = sel["kind"] in ("artist", "playlist")
        try:
            await client.player_queues.queue_command_shuffle(qid, shuffle)
            await client.player_queues.play_media(qid, sel["uri"],
                                                  option=QueueOption.REPLACE)
        except Exception as exc:  # noqa: BLE001
            tried.add(sel["uri"])
            log.warning("play_media rejected %s %r (%s: %s) — re-ranking without it",
                        sel["kind"], sel["name"], type(exc).__name__, exc)
            alt = None
            if len(tried) < _PLAY_ATTEMPTS:
                try:
                    alt = await _resolve_library(query, media_type, exclude=tried)
                except Exception:  # noqa: BLE001 — fallback is best-effort
                    alt = None
            if alt is None:
                raise
            sel, via = alt, "library-fallback"
            continue
        break
    asyncio.create_task(_notify_jukebox_takeover())
    log.info("play_music %r -> %s %r (%s, shuffle=%s, via=%s, score=%s)",
             query, sel["kind"], sel["name"], sel["uri"], shuffle, via,
             sel.get("score"))
    music_log.record(query, "play", winner=sel, via=via,
                     candidates=trace.get("candidates"))
    return {"kind": sel["kind"], "name": sel["name"], "artist": sel.get("artist"),
            "uri": sel["uri"], "shuffle": shuffle}


async def _notify_jukebox_takeover() -> None:
    """Voice just replaced the shared queue's content — tell the NFC jukebox so
    it clears its current-card marker. Without this, re-scanning the last card
    pause-toggles the voice-chosen music instead of playing the card.
    Best-effort: a dead jukebox must never break play."""
    if not config.JUKEBOX_EXTERNAL_PLAY_URL:
        return
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(config.JUKEBOX_EXTERNAL_PLAY_URL,
                               timeout=aiohttp.ClientTimeout(total=4))
    except Exception as exc:  # noqa: BLE001
        log.warning("jukebox external-play notify failed: %s", exc)


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
