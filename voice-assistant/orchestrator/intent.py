"""Intent parsing for the timers vertical slice.

One LLM call, thinking off, temperature 0, strict JSON. Deliberately scoped to
the timer intents for this slice; the schema leaves room for the rest
(lists / HA) to be added later without changing the call shape.
"""

from __future__ import annotations

import logging
import re

from . import clients, config

log = logging.getLogger("orchestrator.intent")

INTENTS = (
    "set_timer", "timer_query", "timer_adjust", "timer_cancel",
    "add_items", "set_reminder", "show_todos", "show_shopping", "complete_item",
    "remove_items", "clear_list", "play_music", "music_control", "music_query",
    "sports", "weather", "business_hours", "place_search", "home_control",
    "broadcast", "find_phone", "ask", "show_answer", "unclear", "none",
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
  "query": for intent "ask" the cleaned question text; for "play_music" the name of what to play; for "business_hours" or "place_search" the named business; for "broadcast" the message to speak; else null,
  "broadcast_target": for "broadcast", the person or room the message is for, close to as spoken ("simon", "the kids", "the loft"); null when none was named,
  "phone_owner": for "find_phone", whose phone as spoken — "my", "brad", "adrienne", "mom", "dad"; else null,
  "phone_action": for "find_phone", "stop" when they say the phone is found or to stop the ringing; else "ring",
  "place_modifier": for "business_hours" or "place_search", a specifically requested department or service such as "tire center", "gas", "pharmacy", or "garden center"; otherwise null,
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
- A timer command that STOPS BEFORE ITS DURATION is still set_timer, with
  duration_seconds null — never "unclear". People pause to think about how long
  they need, and the mic endpoints on the gap: "set a timer", "set the timer
  for", "start a timer", "set a chicken timer for". Keep any label and theme you
  can read ("set a chicken timer" -> label chicken, cluck, duration null). The
  assistant asks for the missing duration itself.
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
  Keep a specifically requested on-site service separate: "when does Costco tire center close"
  -> query "Costco", place_modifier "tire center"; "is Costco gas open" -> query "Costco",
  place_modifier "gas"; "when does the Walgreens pharmacy close" -> query "Walgreens",
  place_modifier "pharmacy". Generic store questions such as "when does Costco close" have a null
  modifier. Do not split a proper business name merely because it contains a service word:
  "when does Discount Tire close" -> query "Discount Tire", place_modifier null.
- place_search = a location/map/distance request about a NAMED business or chain: "where is
  Chipotle" (query "Chipotle"), "show me Home Depot", "are there Costcos nearby", "where's the
  closest Walgreens", "how far is Walmart". Use the clean business name as query. Questions that
  explicitly ask when it opens/closes or whether it is open remain business_hours. Apply the same
  place_modifier rule for an explicitly named department or service, such as "where is Costco gas"
  -> query "Costco", place_modifier "gas".
- home_control = a command to CHANGE something in the house — blinds, lights, lighting modes:
  "close the blinds", "open the left blind", "close the kitchen sink blind", "fix the glare",
  "close the sliding door", "brighten the lights", "make it brighter in here", "dinner mode",
  "set the mood for dinner", "back to normal", "reset the lights". Put the command phrase in
  "query", close to as spoken. Use it for house-change commands even if the target sounds
  unsupported ("open the garage") — the control layer decides what it controls. But music
  playback stays music_control ("turn it up", "stop the music"), and QUESTIONS about the house
  state ("are the blinds closed", "is the kitchen light on") are "ask", never home_control.
- broadcast = relaying a message to a person or room ELSEWHERE in the house over its speaker:
  "tell simon to come eat dinner" -> query "Simon, come eat dinner", broadcast_target "simon";
  "tell the kids it's time to leave" -> query "It's time to leave", broadcast_target "the kids";
  "broadcast that dinner is ready" -> query "Dinner is ready", broadcast_target null;
  "announce to claire that her show is starting" -> query "Claire, your show is starting",
  broadcast_target "claire"; "tell the loft testing one two three" -> query "Testing one two
  three", broadcast_target "the loft". REWRITE the message as natural direct speech addressed
  to the listener — never leave a reported-speech fragment like "to come eat dinner"; when a
  specific person is the target, start the message with their name. "tell me ..." is NEVER
  broadcast ("tell me a joke" is ask, "tell me the weather" is weather); a QUESTION about a
  person is ask ("where is simon", "how old is claire") — broadcast is only for relaying a
  message TO someone.
- find_phone = locating or ringing a lost PHONE (it rings so it can be found): "where's my
  phone" -> find_phone, phone_owner "my", phone_action "ring"; "find adrienne's phone" ->
  phone_owner "adrienne"; "where is mom's phone" -> "mom"; "ring my phone", "make dad's phone
  ring", "I can't find my phone", "I lost my phone". Ending the ringing is phone_action "stop":
  "found it" -> find_phone, phone_action "stop"; "I found my phone" (owner "my", action "stop"),
  "stop ringing the phone", "you can stop the phone now". Only for phones: where a PERSON is
  stays "ask" ("where is simon"), a named BUSINESS stays place_search, and questions ABOUT a
  phone ("is my phone charged") are "ask".
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
- If the command is not a timer, list, music, home-control, or knowledge command, intent "none".
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


# Appended when parsing the answer to a slot question we asked ("for how
# long?"). The reply is a fragment on its own — parsing it alone yields "none"
# and the follow-up gate drops it (live 2026-07-26: "Eight minutes." thrown
# away after "set the timer for"). Stitching it back onto the partial reuses
# the whole rule set above instead of growing a second grammar per slot.
_CLARIFY_NOTE = """
CLARIFY TURN: the user's last command stopped short, so you asked them: \
"{question}"
Their incomplete command was: "{partial}"
The user message below is their reply.
Normally the reply supplies the missing piece: COMBINE the incomplete command \
with the reply and parse the COMBINED command ("set the timer for" + "eight \
minutes" -> set_timer, duration_seconds 480; "set a chicken timer for" + "about \
twenty" -> set_timer, label chicken, 1200, cluck).
But the reply may abandon the request instead. If it is a NEW command or \
question ("actually what's the weather", "play some music"), ignore the \
incomplete command and parse the reply ALONE on its own merits. If it drops the \
request without replacing it ("never mind", "forget it", "hang on"), or it is \
room noise, a fragment, or someone else's conversation, use intent "none"."""


async def parse_clarify(partial: str, reply: str, question: str) -> dict:
    """Parse the answer to a slot question. `partial` is the incomplete command
    we asked about, `question` what we asked. Returns the same validated dict as
    parse() — a completed command, a different command if they moved on, or
    "none" if they dropped it."""
    system = _SYSTEM + "\n" + _CLARIFY_NOTE.format(question=question, partial=partial)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": reply},
    ]
    raw = await clients.parse_intent_raw(messages)
    return _validate(clients.extract_json(raw))


# "set a timer", "set the timer for", "start a chicken timer for" — a timer
# command that ends before its duration. The classifier honestly reads these as
# "unclear", so a narrow regex forces the slot-fill path: the phrase has exactly
# one meaning, and misreading it costs the user the entire turn. Anchored at
# both ends, so anything following "for" (i.e. an actual duration) fails to
# match and takes the normal path.
_TRUNCATED_TIMER_RE = re.compile(
    r"^(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?"
    r"(?:set|start|make|put\s+on)\s+"
    r"(?:a|an|the)?\s*(?:[a-z]+\s+){0,3}timer"
    r"(?:\s+(?:for|to))?$"
)


def is_truncated_timer(command: str) -> bool:
    """True for a timer command cut off before its duration. Deterministic
    backstop for the prompt rule above — prompts are probabilistic, and this
    exact phrasing is the one the family actually hits."""
    return bool(_TRUNCATED_TIMER_RE.match(command.strip().lower().rstrip(".!?,")))


_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}
_UNIT_SECONDS = {
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "second": 1, "seconds": 1, "sec": 1, "secs": 1,
}
# Words that can pad a spoken duration without changing it. Anything outside
# this set means the reply isn't purely a duration -> hand it to the LLM.
_DURATION_FILLER = {"and", "for", "about", "around", "roughly", "like",
                    "make", "it", "just", "maybe", "um", "uh", "please", "say"}


def spoken_duration(text: str) -> int | None:
    """Total seconds from a bare spoken duration — "eight minutes", "90
    seconds", "an hour and a half", "half an hour", "two and a half minutes" —
    or None if the reply is anything else.

    Fast path for the clarify turn: the overwhelmingly common answer to "for how
    long?" is a plain duration, and this skips a ~2s LLM round trip on it. It
    only has to be RIGHT, not complete — every miss falls through to the parser,
    so unrecognised phrasings cost latency, not correctness. A bare number with
    no unit ("eight") deliberately returns None: the unit is a guess, and the
    parser has the original command for context.
    """
    words = re.findall(r"[a-z0-9]+", text.lower())
    total = 0
    num: int | None = None
    article = False     # the current num came from "a"/"an", not a real count
    half = False
    last_unit = 0
    saw_unit = False
    for w in words:
        if w in ("a", "an"):
            # Article, not a quantity: "half an hour" is 30 minutes, not 90.
            if num is None and not half:
                num, article = 1, True
            continue
        if w == "half":
            half = True
            if article:
                num, article = None, False   # "...and a half" — drop the article
            continue
        if w.isdigit():
            num, article = int(w), False
            continue
        if w in _NUM_WORDS:
            n = _NUM_WORDS[w]
            # "twenty five" -> 25; anything else replaces.
            num = num + n if (num and num >= 20 and num % 10 == 0 and n < 10) else n
            article = False
            continue
        if w in _UNIT_SECONDS:
            mult = _UNIT_SECONDS[w]
            total += int(((num or 0) + (0.5 if half else 0)) * mult)
            last_unit, saw_unit = mult, True
            num, half, article = None, False, False
            continue
        if w in _DURATION_FILLER:
            continue
        return None                 # not a pure duration -> let the LLM read it
    if half and saw_unit:
        total += int(0.5 * last_unit)   # trailing "...and a half"
    if not saw_unit or total <= 0:
        return None
    return total


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


def validate(data: dict) -> dict:
    """Coerce a partial intent dict into the full validated shape. Public so a
    deterministic shortcut (the clarify duration fast path) can build a parse
    result without inventing its own field set."""
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

    broadcast_target = data.get("broadcast_target")
    if isinstance(broadcast_target, str):
        broadcast_target = broadcast_target.strip().lower() or None
    else:
        broadcast_target = None

    phone_owner = data.get("phone_owner")
    if isinstance(phone_owner, str):
        phone_owner = phone_owner.strip().lower() or None
    else:
        phone_owner = None

    phone_action = data.get("phone_action")
    if phone_action != "stop":
        phone_action = "ring"

    place_modifier = data.get("place_modifier")
    if isinstance(place_modifier, str):
        place_modifier = place_modifier.strip().lower() or None
    else:
        place_modifier = None

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
        "broadcast_target": broadcast_target,
        "phone_owner": phone_owner,
        "phone_action": phone_action,
        "place_modifier": place_modifier,
        "item_text": item_text,
        "list_type": list_type,
        "media_type": media_type,
        "music_action": music_action,
        "sports_action": sports_action,
        "sports_date": sports_date,
        "weather_when": weather_when,
        "hours_when": hours_when,
    }
