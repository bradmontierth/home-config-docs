"""Intent parsing for the timers vertical slice.

One LLM call, thinking off, temperature 0, strict JSON. Deliberately scoped to
the timer intents for this slice; the schema leaves room for the rest
(lists / HA) to be added later without changing the call shape.
"""

from __future__ import annotations

import logging

from . import clients, config

log = logging.getLogger("orchestrator.intent")

INTENTS = (
    "set_timer", "timer_query", "timer_adjust", "timer_cancel",
    "add_items", "set_reminder", "show_todos", "show_shopping", "complete_item",
    "remove_items", "clear_list", "play_music", "music_control", "music_query",
    "sports", "weather", "business_hours", "place_search", "ask", "show_answer",
    "unclear", "none",
)

WEATHER_WHEN = ("now", "today", "tonight", "tomorrow", "monday", "tuesday",
                "wednesday", "thursday", "friday", "saturday", "sunday")

HOURS_WHEN = ("open", "close", "now", "today")

MUSIC_ACTIONS = ("pause", "resume", "stop", "next", "previous",
                 "volume_up", "volume_down")

_SYSTEM = f"""You are the intent parser for a kitchen assistant. Convert the \
user's command into a single strict JSON object and output ONLY that JSON — no \
prose, no code fences, no explanation.

Schema:
{{
  "intent": one of {list(INTENTS)},
  "label": short lowercase name of the timer (e.g. "chicken", "rice", "pasta"), or null if the user gave none,
  "duration_seconds": integer total seconds for set_timer / timer_adjust (adjust may be negative to remove time), else null,
  "sound_theme": one of {list(config.SOUND_THEMES)},
  "scope": "one" or "all" — "all" only when the user clearly means every timer (e.g. "cancel all timers"), else "one",
  "query": for intent "ask" the cleaned question text; for "play_music" the name of what to play; for "business_hours" or "place_search" the named business; else null,
  "item_text": for complete_item / remove_items, a phrase describing WHICH item(s) to act on — one item ("eggs"), several ("milk and bread"), a category ("the dairy", "produce"), or a property ("everything orange", "all of it"); else null,
  "list_type": for clear_list / show, which list — "shopping", "todo", or "all"; else null,
  "media_type": for play_music, only when the user NAMES a type — "artist", "album", "track", or "playlist"; else null,
  "music_action": for music_control, one of {list(MUSIC_ACTIONS)}; else null,
  "sports_action": for sports, "last" (score/result of the most recent or current game) or "next" (upcoming game); else null,
  "sports_date": for sports, "today" or "yesterday" only when the user SAYS a day like that ("last night" = "yesterday"); else null,
  "weather_when": for weather, one of {list(WEATHER_WHEN)}; else null,
  "hours_when": for business_hours, one of {list(HOURS_WHEN)}; else null
}}

Rules:
- "set a chicken timer for 12 minutes" -> set_timer, label chicken, 720, cluck.
- Pick sound_theme by the food: poultry->cluck, beef/dairy->moo, frying/searing->sizzle,
  boiling/pasta/rice->steam_whistle, simmering/sauce->bubbling, baking/roasting->oven_ding.
  If unclear or no food, use marimba.
- timer_query = "how much time is left", "how long on the rice". duration_seconds null.
- timer_adjust = "add 5 minutes to the rice" -> duration_seconds 300; "take 2 minutes off" -> -120.
- timer_cancel = "cancel the rice timer" (scope one) or "cancel all timers" (scope all).
- sports = a score, result, or upcoming-game question about a NAMED team or league: "what was the
  score of the jazz game" (query "jazz", sports_action "last"), "who won the world cup game
  yesterday" (query "world cup", "last", sports_date "yesterday"), "did the cubs win last night"
  (query "cubs", "last", "yesterday"), "when do the brewers play next" (query "brewers", "next"),
  "are there any nba games tonight" (query "nba", "next"). Put the team or league name in "query".
  If the team is only a pronoun ("when do they play next") or the question is about stats, rosters,
  standings, or history rather than a game result/schedule, use "ask" instead.
- weather = the LOCAL weather here at home, current or forecast: "what's the weather", "how hot is
  it outside", "what's the temperature outside", "is it windy" (weather_when "now"); "what's the
  forecast", "will it rain today" ("today"); "how cold does it get tonight" ("tonight"); "what's
  the weather tomorrow" ("tomorrow"); "what's the forecast for saturday" ("saturday"). Current
  conditions and "is it..." questions -> "now"; forecast questions with no day named -> "today".
  A follow-up like "what about tomorrow" right after a weather answer is weather too. But weather
  for a NAMED other place ("weather in Chicago") or beyond a week out is "ask", not weather.
- business_hours = opening/closing-hours questions about a NAMED business: "what time does Home
  Depot close" (query "Home Depot", hours_when "close"), "when does Costco open" ("Costco",
  "open"), "is Walmart open" ("Walmart", "now"), "what are Smith's hours today" ("Smith's",
  "today"). A pronoun-only follow-up such as "when does it open" remains "ask" in v1 because the
  knowledge service has the prior answer context. Do not use this intent for an unnamed business.
- place_search = a location/map/distance request about a NAMED business or chain: "where is
  Chipotle" (query "Chipotle"), "show me Home Depot", "are there Costcos nearby", "where's the
  closest Walgreens", "how far is Walmart". Use the clean business name as query. Questions that
  explicitly ask when it opens/closes or whether it is open remain business_hours.
- ask = a general knowledge or factual question NOT about timers: "how many tablespoons in a cup",
  "when do babies start walking", "what temperature is chicken done at", "how do I dice an onion".
  Put the cleaned question in "query". No keyword is needed — natural questions route here.
  A question that refers back to an earlier exchange is STILL ask, even if it can't stand alone:
  "when do they play next", "but who's playing in it", "what about tomorrow", "how old is she".
  The knowledge service keeps the conversation history — pass the question through in "query"
  as spoken; never route a question to "unclear" just because its subject is a pronoun.
- add_items = adding things to a shopping or todo list: "add eggs and milk to the shopping list",
  "put paper towels on the list", "add a todo to call the plumber". Leave label/query/item_text null;
  the full command is forwarded to the list service, which figures out the items itself.
- set_reminder = a time-based reminder: "remind me to take the roast out at 5", "remind me to call mom
  tomorrow morning". Also forwarded whole; leave item_text null.
- show_todos = "show my todos", "what's on my to-do list". show_shopping = "show the shopping list",
  "what do I need to buy". No other fields.
- complete_item = checking something off: "mark eggs as done", "I bought the milk", "cross off call
  the dentist", "I got the dairy". Put the description in "item_text" (may name one item or many).
- remove_items = removing item(s) or UNDOING the last add: "take the eggs off the list", "remove milk
  and bread", "remove everything orange", "take off the produce", "scratch that", "undo". Put the
  target description in "item_text" — including categories ("the dairy") and properties ("everything
  orange"). If they mean whatever was just added ("scratch my last", "undo", "remove that"), leave
  "item_text" null. Different from complete_item, which marks something DONE rather than removing it.
- clear_list = emptying a whole list: "clear the shopping list", "clear my todos", "delete everything
  on the list", "empty the list". Set "list_type" to "shopping", "todo", or "all". NOT the same as
  remove_items (which targets specific items); clear_list wipes the entire list.
- play_music = starting music: "play raffi", "play baby beluga", "put on the wheels on the bus",
  "play the album baby beluga", "play some music". Put WHAT to play in "query" — just the name, with
  filler dropped ("play the album baby beluga" -> query "baby beluga", media_type "album"; "play some
  raffi" -> query "raffi"). If they only want music with nothing named ("play some music", "turn on
  the music"), leave query null. media_type stays null unless they LITERALLY SAY the word "artist",
  "album", "song"/"track", or "playlist" — never infer it from the name ("play the best of raffi"
  -> query "the best of raffi", media_type null, even though it sounds like an album).
- music_control = controlling playback that's already going. Map to "music_action": "pause" the
  music -> pause; "keep playing" / "unpause" / "resume" -> resume; "stop the music" / "turn off the
  music" -> stop; "skip" / "next song" / "skip this" -> next; "go back" / "previous song" -> previous;
  "turn it up" / "louder" -> volume_up; "turn it down" / "quieter" -> volume_down.
  A bare "pause" or "stop" with no object is music_control too (timers get cancelled, not stopped).
- music_query = asking about the current song: "what's playing", "what song is this", "who sings this".
- show_answer = re-show or repeat the assistant's PREVIOUS answer, adding nothing new: "show that
  answer again", "bring that answer back", "put that back up", "show me that again", "what did you
  just say", "repeat that", "say that again". No other fields. A NEW question about the same topic
  ("but who's playing in it") is "ask", not show_answer.
- If the command is not a timer, list, music, or knowledge command, intent "none".
Return JSON only."""

# Appended to the system prompt when parsing a FOLLOW-UP turn (the user kept
# talking after a reply, with no wake word to gate it). Stricter about "none":
# the mic is open to the whole room, so unrelated chatter must be dropped.
_FOLLOWUP_NOTE = """
FOLLOW-UP TURN: this audio came right after your last reply, captured WITHOUT a \
wake word, so it may be a continuation of the conversation OR unrelated \
background speech / someone else's conversation / a stray fragment not addressed \
to you.
Recent context: {context}
Choose the intent by whether the speech is addressed to you:
- If it is a clear command to you, use the matching action intent (e.g. "also \
add butter", "make that fifteen minutes", "and cancel the rice", "scratch my \
last").
- A follow-up QUESTION about your previous answer ("but who's playing in it", \
"when do they play next", "what about tomorrow") is intent "ask" — put it in \
"query" as spoken.
- If it clearly seems addressed to you (a command, a request, second person) \
but you cannot map it to any supported action, use intent "unclear".
- If it is small talk, a fragment, someone else's conversation, or anything not \
directed at you, use intent "none". When it is not clearly for you, prefer \
"none" over "unclear" — do not talk back to the room."""


async def parse(command: str, context: str | None = None) -> dict:
    """Parse a command string into a validated intent dict. When `context` is
    given (a follow-up turn), append the follow-up note so ambiguous/background
    speech routes to "none"."""
    system = _SYSTEM
    if context:
        system = _SYSTEM + "\n" + _FOLLOWUP_NOTE.format(context=context)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": command},
    ]
    raw = await clients.parse_intent_raw(messages)
    data = clients.extract_json(raw)
    return _validate(data)


def _validate(data: dict) -> dict:
    intent = data.get("intent")
    if intent not in INTENTS:
        intent = "none"

    label = data.get("label")
    if isinstance(label, str):
        label = label.strip().lower() or None
    else:
        label = None

    duration = data.get("duration_seconds")
    if not isinstance(duration, (int, float)):
        duration = None
    else:
        duration = int(duration)

    theme = data.get("sound_theme")
    if theme not in config.SOUND_THEMES:
        theme = config.DEFAULT_THEME

    scope = data.get("scope")
    if scope not in ("one", "all"):
        scope = "one"

    query = data.get("query")
    if isinstance(query, str):
        query = query.strip() or None
    else:
        query = None

    item_text = data.get("item_text")
    if isinstance(item_text, str):
        item_text = item_text.strip() or None
    else:
        item_text = None

    list_type = data.get("list_type")
    if list_type not in ("shopping", "todo", "all"):
        list_type = None

    media_type = data.get("media_type")
    if media_type not in ("artist", "album", "track", "playlist"):
        media_type = None

    music_action = data.get("music_action")
    if music_action not in MUSIC_ACTIONS:
        music_action = None

    sports_action = data.get("sports_action")
    if sports_action not in ("last", "next"):
        sports_action = None

    sports_date = data.get("sports_date")
    if sports_date not in ("today", "yesterday"):
        sports_date = None

    weather_when = data.get("weather_when")
    if isinstance(weather_when, str):
        weather_when = weather_when.strip().lower()
    if weather_when not in WEATHER_WHEN:
        weather_when = None

    hours_when = data.get("hours_when")
    if isinstance(hours_when, str):
        hours_when = hours_when.strip().lower()
    if hours_when not in HOURS_WHEN:
        hours_when = None

    return {
        "intent": intent,
        "label": label,
        "duration_seconds": duration,
        "sound_theme": theme,
        "scope": scope,
        "query": query,
        "item_text": item_text,
        "list_type": list_type,
        "media_type": media_type,
        "music_action": music_action,
        "sports_action": sports_action,
        "sports_date": sports_date,
        "weather_when": weather_when,
        "hours_when": hours_when,
    }
