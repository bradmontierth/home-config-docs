"""Sports intent: scores and schedules from ESPN's unofficial API.

This is the structured-data path (how Google Home / Alexa answer scores):
qwen extracts the team/league name, we resolve it against cached ESPN team
lists (rapidfuzz, same approach as the music library resolver), hit the
scoreboard/schedule endpoint, and template the spoken answer. ~1s, free, no
hallucination possible. The endpoints are unofficial (no key, no auth) and
can change without notice — handle() returning None or raising makes app.py
fall back to the smart-model ask path, so breakage degrades to slow-but-right.

Docs (community): https://gist.github.com/akeaswaran/b48b02f1c94f873c6655e7129910fc3b
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from rapidfuzz import fuzz

from . import config

log = logging.getLogger("orchestrator.sports")

_BASE = "https://site.api.espn.com/apis/site/v2/sports"

# league key -> (sport, espn league slug, spoken label, aliases that mean the
# WHOLE league rather than a team)
LEAGUES: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    "nba": ("basketball", "nba", "NBA", ("nba", "basketball")),
    "wnba": ("basketball", "wnba", "WNBA", ("wnba",)),
    "mlb": ("baseball", "mlb", "MLB", ("mlb", "baseball")),
    "nfl": ("football", "nfl", "NFL", ("nfl", "football")),
    "nhl": ("hockey", "nhl", "NHL", ("nhl", "hockey")),
    "mls": ("soccer", "usa.1", "MLS", ("mls",)),
    "worldcup": ("soccer", "fifa.world", "World Cup",
                 ("world cup", "the world cup", "fifa world cup", "fifa")),
}

_TEAM_MATCH_THRESHOLD = 78
_TEAMS_TTL_S = 24 * 3600
_teams_cache: dict[str, tuple[float, list[dict]]] = {}


def _now_local() -> datetime:
    return datetime.now(ZoneInfo(config.ASK_TIMEZONE))


async def _get(client: httpx.AsyncClient, url: str, **params) -> dict:
    r = await client.get(url, params=params or None)
    r.raise_for_status()
    return r.json()


async def _teams(client: httpx.AsyncClient, league_key: str) -> list[dict]:
    """League team list, cached a day. Each entry: {id, names[], spoken}."""
    cached = _teams_cache.get(league_key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    sport, slug, _, _ = LEAGUES[league_key]
    data = await _get(client, f"{_BASE}/{sport}/{slug}/teams", limit=400)
    teams: list[dict] = []
    for item in (data.get("sports", [{}])[0].get("leagues", [{}])[0]
                 .get("teams", [])):
        t = item.get("team", {})
        names = {t.get(k) for k in
                 ("displayName", "shortDisplayName", "name", "location",
                  "abbreviation", "nickname")}
        names = [n for n in names if n]
        if t.get("id") and names:
            teams.append({
                "id": t["id"],
                "names": names,
                # shortDisplayName reads best aloud ("Jazz", "Argentina")
                "spoken": t.get("shortDisplayName") or t.get("displayName"),
                "league": league_key,
            })
    _teams_cache[league_key] = (time.monotonic() + _TEAMS_TTL_S, teams)
    log.info("teams cached for %s: %d", league_key, len(teams))
    return teams


def _match_league(name: str) -> str | None:
    n = name.lower().strip()
    for key, (_, _, _, aliases) in LEAGUES.items():
        if n in aliases:
            return key
    return None


async def _match_team(client: httpx.AsyncClient, name: str) -> dict | None:
    """Fuzzy-match a spoken name against every configured league's teams."""
    best, best_score = None, 0.0
    for key in LEAGUES:
        try:
            teams = await _teams(client, key)
        except Exception as exc:  # noqa: BLE001 — one dead league list is fine
            log.warning("team list fetch failed for %s: %s", key, exc)
            continue
        for t in teams:
            score = max(fuzz.WRatio(name.lower(), n.lower()) for n in t["names"])
            if score > best_score:
                best, best_score = t, score
    if best and best_score >= _TEAM_MATCH_THRESHOLD:
        log.info("team match %r -> %s (%s) score=%.0f",
                 name, best["spoken"], best["league"], best_score)
        return best
    log.info("no team match for %r (best=%.0f)", name, best_score)
    return None


# --- event helpers (scoreboard and schedule endpoints differ slightly) -----
def _status(event: dict) -> dict:
    comp = (event.get("competitions") or [{}])[0]
    st = comp.get("status") or event.get("status") or {}
    return st.get("type", {})


def _competitors(event: dict) -> list[dict]:
    return (event.get("competitions") or [{}])[0].get("competitors", [])


def _score(competitor: dict) -> str | None:
    s = competitor.get("score")
    if isinstance(s, dict):
        return s.get("displayValue")
    if s in (None, ""):
        return None
    return str(s)


def _team_name(competitor: dict) -> str:
    t = competitor.get("team", {})
    return t.get("shortDisplayName") or t.get("displayName") or "unknown"


def _event_dt(event: dict) -> datetime | None:
    raw = event.get("date")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _when_phrase(dt: datetime) -> str:
    """'today at 7 PM' / 'Friday at 6:10 PM' / 'on October 22'."""
    local = dt.astimezone(ZoneInfo(config.ASK_TIMEZONE))
    now = _now_local()
    t = local.strftime("%-I:%M %p").replace(":00 ", " ")
    if local.date() == now.date():
        return f"today at {t}"
    if local.date() == (now + timedelta(days=1)).date():
        return f"tomorrow at {t}"
    if (local.date() - now.date()).days < 7:
        return f"{local.strftime('%A')} at {t}"
    return f"on {local.strftime('%B %-d')}"


def _result_phrase(event: dict) -> str | None:
    """One completed/live game -> spoken sentence."""
    comps = _competitors(event)
    if len(comps) != 2:
        return None
    st = _status(event)
    a, b = comps
    sa, sb = _score(a), _score(b)
    if st.get("state") == "in":
        def _num(s: str | None) -> float:
            try:
                return float(s)
            except (TypeError, ValueError):
                return 0.0
        lead, trail = (a, b) if _num(sa) >= _num(sb) else (b, a)
        return (f"{_team_name(lead)} {_score(lead)}, {_team_name(trail)} "
                f"{_score(trail)} right now — {st.get('shortDetail', 'in progress')}")
    if not st.get("completed"):
        return None
    winner = next((c for c in comps if c.get("winner")), None)
    loser = next((c for c in comps if c is not winner), None)
    if winner is None or sa is None or sb is None:
        return None
    if sa == sb:  # drawn in regulation -> decided on penalties (soccer)
        return (f"{_team_name(winner)} beat {_team_name(loser)} on penalties "
                f"after a {sa} to {sb} draw")
    return (f"{_team_name(winner)} beat {_team_name(loser)} "
            f"{_score(winner)} to {_score(loser)}")


def _matchup_phrase(event: dict) -> str | None:
    comps = _competitors(event)
    if len(comps) != 2:
        return None
    home = next((c for c in comps if c.get("homeAway") == "home"), comps[0])
    away = next((c for c in comps if c is not home), comps[1])
    dt = _event_dt(event)
    when = f" {_when_phrase(dt)}" if dt else ""
    return f"{_team_name(away)} at {_team_name(home)}{when}"


# --- the two lookup shapes --------------------------------------------------
async def _team_answer(client: httpx.AsyncClient, team: dict,
                       action: str) -> str | None:
    sport, slug, _, _ = LEAGUES[team["league"]]
    data = await _get(
        client, f"{_BASE}/{sport}/{slug}/teams/{team['id']}/schedule")
    events = data.get("events", [])
    if action == "next":
        upcoming = sorted(
            (e for e in events if _status(e).get("state") == "pre"),
            key=lambda e: e.get("date", ""))
        if not upcoming:
            return (f"The {team['spoken']} don't have a game scheduled — "
                    "the season may be over or not started yet.")
        phrase = _matchup_phrase(upcoming[0])
        return f"The {team['spoken']} play next: {phrase}." if phrase else None
    # action == "last": most recent live or completed game
    live = [e for e in events if _status(e).get("state") == "in"]
    if live:
        phrase = _result_phrase(live[0])
        return f"{phrase}." if phrase else None
    done = sorted(
        (e for e in events if _status(e).get("completed")),
        key=lambda e: e.get("date", ""), reverse=True)
    if not done:
        return (f"I don't see any recent {team['spoken']} games — "
                "the season may not have started.")
    phrase = _result_phrase(done[0])
    if not phrase:
        return None
    dt = _event_dt(done[0])
    ago = ""
    if dt:
        days = (_now_local().date() - dt.astimezone(
            ZoneInfo(config.ASK_TIMEZONE)).date()).days
        ago = " today" if days == 0 else " yesterday" if days == 1 else (
            f" on {dt.astimezone(ZoneInfo(config.ASK_TIMEZONE)).strftime('%A')}"
            if days < 7 else "")
    return f"{phrase}{ago}."


async def _league_answer(client: httpx.AsyncClient, league_key: str,
                         action: str, date_word: str | None) -> str | None:
    sport, slug, label, _ = LEAGUES[league_key]
    url = f"{_BASE}/{sport}/{slug}/scoreboard"
    today = _now_local().date()
    if action == "next":
        span = f"{today:%Y%m%d}-{today + timedelta(days=7):%Y%m%d}"
        data = await _get(client, url, dates=span)
        upcoming = sorted(
            (e for e in data.get("events", [])
             if _status(e).get("state") == "pre"),
            key=lambda e: e.get("date", ""))
        if not upcoming:
            return f"I don't see any {label} games in the next week."
        first_day = _event_dt(upcoming[0])
        if first_day:
            tz = ZoneInfo(config.ASK_TIMEZONE)
            same_day = [e for e in upcoming
                        if _event_dt(e) and _event_dt(e).astimezone(tz).date()
                        == first_day.astimezone(tz).date()]
        else:
            same_day = upcoming
        phrases = [p for p in (_matchup_phrase(e) for e in same_day[:4]) if p]
        return f"Next {label} games: " + "; ".join(phrases) + "."
    # action == "last": a specific day, or today-then-yesterday
    days = [today - timedelta(days=1)] if date_word == "yesterday" else \
        [today] if date_word == "today" else [today, today - timedelta(days=1)]
    for day in days:
        data = await _get(client, url, dates=f"{day:%Y%m%d}")
        events = data.get("events", [])
        results = [p for p in (_result_phrase(e) for e in events) if p]
        if results:
            when = "today" if day == today else "yesterday"
            return f"{label} {when}: " + "; ".join(results[:5]) + "."
        if events and all(_status(e).get("state") == "pre" for e in events):
            when = "today" if day == today else "yesterday"
            phrases = [p for p in (_matchup_phrase(e) for e in events[:3]) if p]
            return (f"No finished {label} games {when} yet. Coming up: "
                    + "; ".join(phrases) + ".")
    when = ("yesterday" if date_word == "yesterday" else
            "today" if date_word == "today" else "today or yesterday")
    return f"There were no {label} games {when}."


async def handle(parsed: dict) -> dict | None:
    """Answer a sports intent from structured data. Returns a result dict for
    app.py, or None when we can't resolve it (caller falls back to ask)."""
    name = (parsed.get("query") or "").strip()
    if not name:
        return None
    action = parsed.get("sports_action") or "last"
    date_word = parsed.get("sports_date")
    async with httpx.AsyncClient(timeout=8) as client:
        league_key = _match_league(name)
        if league_key:
            spoken = await _league_answer(client, league_key, action, date_word)
        else:
            team = await _match_team(client, name)
            if not team:
                return None
            spoken = await _team_answer(client, team, action)
    if not spoken:
        return None
    return {"response": spoken, "ok": True}
