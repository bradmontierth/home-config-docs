"""Intent parsing for the timers vertical slice.

One LLM call, thinking off, temperature 0, strict JSON. Deliberately scoped to
the timer intents for this slice; the schema leaves room for the rest
(lists / HA) to be added later without changing the call shape.
"""

from __future__ import annotations

import logging
import re

from . import camera, clients, clock, config, zones

log = logging.getLogger("orchestrator.intent")

INTENTS = (
    "set_timer", "timer_query", "timer_adjust", "timer_rename", "timer_cancel",
    "add_items", "set_reminder", "show_todos", "show_shopping", "show_reminders",
    "complete_item",
    "remove_items", "clear_list", "play_music", "music_control", "music_query",
    "sports", "weather", "time_query", "business_hours", "place_search",
    "home_control",
    "broadcast", "find_phone", "show_camera", "close_camera",
    "ask", "show_answer", "unclear", "none",
)

CAMERA_TARGETS = ("simon", "claire")

WEATHER_WHEN = ("now", "today", "tonight", "tomorrow", "monday", "tuesday",
                "wednesday", "thursday", "friday", "saturday", "sunday")

HOURS_WHEN = ("open", "close", "now", "today")

MUSIC_ACTIONS = ("pause", "resume", "stop", "next", "previous",
                 "volume_up", "volume_down", "volume_set", "volume_normal")

_SYSTEM = f"""You are the intent parser for a household voice assistant. Convert the \
user's command into a single strict JSON object and output ONLY that JSON — no \
prose, no code fences, no explanation.

Schema:
{{
  "intent": one of {list(INTENTS)},
  "label": short lowercase name of the timer (e.g. "chicken", "rice", "pasta"), or null if the user gave none,
  "new_label": for timer_rename, the new short lowercase timer name without the word "timer"; null when the command stops before the new name,
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
  "missing_content": for add_items / set_reminder, true when the command stops before naming WHAT to add; else false,
  "media_type": for play_music, only when the user NAMES a type — "artist", "album", "track", or "playlist"; else null,
  "music_action": for music_control, one of {list(MUSIC_ACTIONS)}; else null,
  "music_volume": for music_control with music_action "volume_set", the requested integer from 0 to 100; else null,
  "sports_action": for sports, "last" (score/result of the most recent or current game) or "next" (upcoming game); else null,
  "sports_date": for sports, "today" or "yesterday" only when the user SAYS a day like that ("last night" = "yesterday"); else null,
  "weather_when": for weather, one of {list(WEATHER_WHEN)}; else null,
  "weather_location": for weather at a NAMED place away from home, the city/place as spoken (include state/country when given); null for local home weather,
  "time_kind": for time_query, what they asked for — one of {list(clock.KINDS)}; else null,
  "time_day": for time_query, one of {list(clock.DAYS)} — "today" unless they name another day; else null,
  "hours_when": for business_hours, one of {list(HOURS_WHEN)}; else null,
  "camera_target": for "show_camera", which child's camera — one of {list(CAMERA_TARGETS)}; else null
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
- timer_rename changes only a timer's name: "rename the timer to pasta" -> label null,
  new_label "pasta"; "rename the chicken timer to pasta" -> label "chicken", new_label
  "pasta"; "change the timer to be called pasta" and "call the timer pasta" are also
  timer_rename. A rename command that STOPS BEFORE THE NEW NAME ("rename the timer to",
  "change the timer to be called", "change the timer to be a") is still timer_rename with
  new_label null so the assistant can ask what to call it. Do not treat "change the timer to
  ten minutes" as rename; that concerns duration and is timer_adjust.
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
  A follow-up like "what about tomorrow" right after a weather answer is weather too. Weather for
  a NAMED other place is also weather and MUST keep the place in weather_location: "weather in
  Chicago" -> weather, now, "Chicago"; "what's the weather in Park City today" -> weather, today,
  "Park City"; "forecast for Paris tomorrow" -> weather, tomorrow, "Paris". Only forecasts beyond
  the supported week are "ask".
- time_query = what time, day, or date it is RIGHT HERE, read off the clock: "what time is it",
  "what's the time" (time_kind "time"); "what's the date", "what's today's date", "what day is
  it", "what's today" (time_kind "date" — people say "day" when they want the whole date, so both
  get "date"); "what month is it" ("month"); "what year is it" ("year"). Use "weekday" ONLY when
  they say "of the week" ("what day of the week is it") — every other "what day" is "date".
  Set time_day only when they name tomorrow or yesterday: "what's tomorrow's date" ->
  date/"tomorrow", "what day was yesterday" -> date/"yesterday"; otherwise "today". Any OTHER day
  they name ("what's the date on friday", "what day is the 4th", "what's the day after tomorrow")
  is NOT time_query — it is ask, which can work the date out. The time in
  ANOTHER place ("what time is it in London") is ask, and a time that belongs to an event or a
  business stays with its own intent: "what time does the game start" is sports, "what time does
  Costco close" is business_hours, "how much time is left" is timer_query.
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
  "close the blinds", "open the left blind", "close the sink blind", "fix the glare",
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
- An add or reminder command that STOPS BEFORE NAMING WHAT is still add_items / set_reminder, with
  "missing_content" true — never "unclear". People pause to compose the thing itself and the mic
  endpoints on the gap: "remind me to", "remind me", "set a reminder", "add to my to-do list", "put
  on the shopping list". The assistant asks what to add itself. A command that DOES name something
  ("remind me to call mom", "add eggs to the list") has missing_content false.
- show_todos = "show my todos", "what's on my to-do list". show_shopping = "show the shopping list",
  "what do I need to buy". show_reminders = "show my reminders", "what reminders do I have", "what
  am I being reminded about", "do I have any reminders", "what's my next reminder". No other fields.
  A request to SET one ("remind me to…") is set_reminder, never show_reminders.
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
  "turn it up" / "louder" -> volume_up; "turn it down" / "quieter" -> volume_down;
  "set the volume to 80" / "volume eighty" -> volume_set with music_volume 80;
  "normal volume" / "back to normal volume" -> volume_normal.
  A bare "pause" or "stop" with no object is music_control too (timers get cancelled, not stopped).
- music_query = asking about the current song: "what's playing", "what song is this", "who sings this".
- show_camera = put a CHILD's camera on the kitchen display: "show me Simon", "show me Claire",
  "pull up Simon's camera", "let me see Claire", "put Simon's room on the screen", "check on
  Claire", "I want to see Simon". Set camera_target to "simon" or "claire". Only these two
  people have cameras. Note "show me <person>" is show_camera, while "show me <business>"
  ("show me Home Depot") stays place_search — a person is not a place. Sending a child a
  spoken message is still broadcast ("tell Simon to come eat"), and changing something in
  their room is still home_control ("turn on Simon's lights").
  show_camera means ONLY "put a live camera on the screen". A question about DISTANCE,
  HOURS, or LOCATION is never show_camera even when the name matches a child — some real
  store names collide with them: "how far is Claire's" -> place_search, "is Claire's open"
  -> business_hours, "show me Simon Mall" -> place_search.
- close_camera = dismiss that camera view and give the display back: "close the camera",
  "stop the camera", "turn off the camera", "close the video", "hide the camera",
  "I'm done with the camera", "close Simon", "go back". No other fields.
- show_answer = re-show or repeat the assistant's PREVIOUS answer, adding nothing new: "show that
  answer again", "bring that answer back", "put that back up", "show me that again", "what did you
  just say", "repeat that", "say that again". No other fields. A NEW question about the same topic
  ("but who's playing in it") is "ask", not show_answer.
- If the command is not a timer, list, music, home-control, or knowledge command, intent "none".
Return JSON only."""


def _system(sat: str | None = None) -> str:
    """Global intent grammar plus the one piece of routing context the model
    may need. Device inventories and other rooms' aliases stay outside the
    prompt; deterministic room-scoped matching owns those."""
    room = zones.spoken_for(sat)
    return (
        _SYSTEM
        + f"\n\nORIGIN ROOM: {room}. This identifies where the command was spoken. "
          "Do not assume devices that are not named, and do not import context "
          "from any other room."
    )

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
twenty" -> set_timer, label chicken, 1200, cluck; "remind me to" + "call the \
dentist at five" -> set_reminder, missing_content false; "rename the timer to" + \
"pasta timer" -> timer_rename, new_label pasta).
But the reply may abandon the request instead. If it is a NEW command or \
question ("actually what's the weather", "play some music"), ignore the \
incomplete command and parse the reply ALONE on its own merits. If it drops the \
request without replacing it ("never mind", "forget it", "hang on"), or it is \
room noise, a fragment, or someone else's conversation, use intent "none"."""


async def parse_clarify(partial: str, reply: str, question: str,
                        sat: str | None = None) -> dict:
    """Parse the answer to a slot question. `partial` is the incomplete command
    we asked about, `question` what we asked. Returns the same validated dict as
    parse() — a completed command, a different command if they moved on, or
    "none" if they dropped it."""
    system = _system(sat) + "\n" + _CLARIFY_NOTE.format(
        question=question, partial=partial)
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


# The same trick one slot over: an add/reminder command that ends before the
# thing itself. Live 2026-07-27 — Adrienne said "remind me to", paused to
# compose the reminder, and Silero endpointed on the gap. Anchored at both
# ends, so anything naming actual content fails to match and takes the normal
# path. Safe to hard-code for exactly the reason the timer one is: each of
# these phrasings has one meaning once it stops where it does. NOT a pattern to
# reach for in general intent routing.
_TRUNCATED_REMINDER_RE = re.compile(
    r"^(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?"
    r"(?:remind\s+me(?:\s+(?:to|that))?"
    r"|(?:set|add|make|create)\s+(?:me\s+)?(?:a|an|the)?\s*reminder"
    r"(?:\s+(?:to|for|that))?)$"
)
_LIST_NOUN = r"(?:to.?do|todo|shopping|grocery)"
_LIST_TAIL = rf"(?:to|on)\s+(?:my|the|our)\s+(?:{_LIST_NOUN}\s+)?list"
_TRUNCATED_TODO_RE = re.compile(
    rf"^(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?"
    rf"(?:add|put)\s+(?:something\s+)?"
    rf"(?:(?:a|an)\s+(?:new\s+)?(?:{_LIST_NOUN}|item)(?:\s+{_LIST_TAIL})?"
    rf"|{_LIST_TAIL})$"
)


def is_truncated_add(command: str) -> str | None:
    """Which list intent a command that stopped before naming its content
    belongs to — "set_reminder", "add_items", or None when the command names
    something (or isn't one of these at all). Deterministic backstop for the
    prompt rule above, exactly like is_truncated_timer."""
    text = command.strip().lower().rstrip(".!?,")
    if _TRUNCATED_REMINDER_RE.match(text):
        return "set_reminder"
    if _TRUNCATED_TODO_RE.match(text):
        return "add_items"
    return None


# "show MY to-dos" wants one person's list; "show THE to-dos" / "our to-dos"
# wants the household's. Possessive phrasing is the entire signal, read here
# rather than by the classifier so the same words always scope the same way.
_POSSESSIVE_RE = re.compile(r"\b(?:my|mine)\b")


def wants_own_list(command: str) -> bool:
    """True when the speaker asked for THEIR list rather than the house's."""
    return bool(_POSSESSIVE_RE.search(command.lower()))


# The explicit opt-out from the kitchen display ("remind me privately to…").
# Deliberately narrow: a bare "private" would fire on innocent content ("the
# private school tour"), and a privacy switch that triggers by accident is
# worse than one the user has to say plainly.
_PRIVATE_RE = re.compile(
    r"\bprivately\b|\bin private\b|\bprivate (?:reminder|note|to.?do|todo)\b")


def wants_private(command: str) -> bool:
    """True when the speaker asked to keep this item off the kitchen screen."""
    return bool(_PRIVATE_RE.search(command.lower()))


_NUM_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
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


def spoken_music_volume(text: str) -> int | None:
    """An explicit 0-100 value immediately following the word ``volume``.

    Deliberately narrower than a general number parser: the classifier must
    already have called the utterance music control before this value is used,
    and anchoring it to ``volume`` keeps unrelated questions containing a
    number from becoming player commands. Handles the forms the ASR emits in
    practice: "volume 80", "volume to eighty", and "volume forty five
    percent".
    """
    words = re.findall(r"[a-z0-9]+", text.lower())
    try:
        pos = words.index("volume") + 1
    except ValueError:
        return None
    while pos < len(words) and words[pos] in ("to", "at", "the", "level"):
        pos += 1
    if pos >= len(words):
        return None
    word = words[pos]
    if word.isdigit():
        level = int(word)
    elif word in _NUM_WORDS:
        level = _NUM_WORDS[word]
        # "forty five"; a second standalone number outside the 20/30/... tens
        # shape is not one volume value and is left to the classifier.
        if (level >= 20 and level % 10 == 0 and pos + 1 < len(words)
                and words[pos + 1] in _NUM_WORDS
                and 0 < _NUM_WORDS[words[pos + 1]] < 10):
            level += _NUM_WORDS[words[pos + 1]]
    else:
        return None
    return level if 0 <= level <= 100 else None


# Deterministic pre-classifier grammar. These phrases have one complete,
# supported interpretation; anything outside the narrow shapes returns None
# and pays the normal classifier round trip. Kept on fresh wake turns only by
# app.handle_command -- follow-ups need their conversation context.
_FAST_LEAD = re.compile(
    r"^(?:(?:please|hey|ok|okay|can you|could you|would you|will you)\s+)+")
_FAST_TAIL = re.compile(r"(?:\s+(?:please|for me|thanks|thank you))+$")
_FAST_TIMER = re.compile(r"^(?:set|start)\s+(?:(?:a|the)\s+)?timer\s+for\s+(.+)$")

# "show me Simon" is said the same way every time, and it sits directly beside
# place_search ("show me Home Depot") in the classifier's view — one is a
# person, the other a business, and the only thing separating them is knowing
# who lives here. Matching the kid cameras deterministically keeps them off
# that coin flip, and off an LLM round trip.
_FAST_CAMERA = re.compile(
    r"^(?:show|pull up|bring up|put up|put|let me see|i want to see|i wanna see|"
    r"check on|look in on|look at)\s+"
    r"(?:me\s+)?(?:on\s+)?(?:the\s+)?(?:camera\s+(?:for|in|on)\s+)?(?:the\s+)?"
    r"(?P<who>simon'?s?|claire'?s?|clare'?s?)"
    r"(?:\s+(?:room|cam|camera|feed|video|monitor))?"
    r"(?:\s+on\s+the\s+(?:screen|display|tv))?$"
)

_FAST_CAMERA_CLOSE = frozenset({
    "close the camera", "close camera", "close the cameras",
    "stop the camera", "stop camera", "close the video", "stop the video",
    "turn off the camera", "turn the camera off", "shut off the camera",
    "hide the camera", "close the feed", "close the monitor",
    "exit the camera", "get rid of the camera",
    "done with the camera", "i'm done with the camera", "im done with the camera",
    "close simon", "close claire",
})

# Deliberately NOT in the fast parser: these mean "close the camera" only while
# a camera is actually on screen, and mean nothing (or something else) the rest
# of the time. app.handle_command checks the display before honouring them.
CAMERA_BACK_PHRASES = frozenset({
    "go back", "back", "close it", "take it down", "get me out of this",
})

_MUSIC_CONTROL_ALIASES = {
    "pause the music": "pause", "pause music": "pause",
    "resume the music": "resume", "resume music": "resume",
    "keep playing": "resume", "unpause the music": "resume",
    "stop the music": "stop", "stop music": "stop",
    "turn off the music": "stop",
    "skip": "next", "skip this": "next", "skip this song": "next",
    "next song": "next", "play the next song": "next",
    "previous song": "previous", "go back a song": "previous",
    "go to the previous song": "previous",
    "louder": "volume_up", "turn it up": "volume_up",
    "turn up the music": "volume_up", "music louder": "volume_up",
    "quieter": "volume_down", "turn it down": "volume_down",
    "turn down the music": "volume_down", "music quieter": "volume_down",
    "normal volume": "volume_normal", "back to normal volume": "volume_normal",
    "put the volume back": "volume_normal",
}
_MUSIC_QUERY_ALIASES = {
    "what's playing", "what is playing", "what song is this",
    "what's this song", "what is this song", "who sings this",
    "who is singing this",
}
_WEATHER_NOW = {
    "what's the weather", "what is the weather", "how's the weather",
    "how is the weather", "what's it like outside", "what is it like outside",
    "what's the temperature", "what is the temperature",
    "what's the temperature outside", "what is the temperature outside",
    "how hot is it outside", "how cold is it outside", "is it windy",
}
_WEATHER_TODAY = {"what's the forecast", "what is the forecast"}
_WEATHER_DAYS = "|".join(WEATHER_WHEN[1:])
_WEATHER_DAY_RE = re.compile(
    rf"^(?:what(?:'s| is)\s+the\s+)?(?:weather|forecast)(?:\s+for)?\s+"
    rf"(?P<when>{_WEATHER_DAYS})$")
_WEATHER_PRECIP_RE = re.compile(
    rf"^will\s+it\s+(?:rain|snow)(?:\s+on)?\s+(?P<when>{_WEATHER_DAYS})$")
_WEATHER_LOCATION_RE = re.compile(
    rf"^(?:(?:what(?:'s| is)\s+the)\s+)?(?P<kind>weather|forecast)\s+"
    rf"(?:in|for)\s+(?P<location>.+?)"
    rf"(?:\s+(?:(?:for|on)\s+)?(?P<when>{_WEATHER_DAYS}))?$")
_WEATHER_LOCATION_PRECIP_RE = re.compile(
    rf"^will\s+it\s+(?:rain|snow)\s+in\s+(?P<location>.+?)"
    rf"(?:\s+(?:on\s+)?(?P<when>{_WEATHER_DAYS}))?$")

# Clock questions are asked in a small, fixed set of ways, so they match on
# whole normalized utterances rather than a pattern. That anchoring is the
# point: "what time does Costco close", "what time is it in Tokyo" and "what
# day are we leaving" must all MISS and reach the classifier, and a phrase
# table can't half-match its way into answering them.
_CLOCK_LEAD = re.compile(
    r"^(?:(?:do you know|do you have|tell me|remind me)\s+)+")
# "today" is deliberately NOT stripped here — it is the subject of "what day is
# today", not filler, and trimming it would leave a fragment that matches
# nothing. The today-suffixed phrasings are spelled out in the table instead.
_CLOCK_TAIL = re.compile(r"(?:\s+(?:right now|now|again))+$")
_CLOCK_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("time", "today", (
        "what time is it", "what time it is", "whats the time",
        "what is the time", "the time", "what time do you have",
        "whats the current time", "what is the current time",
    )),
    # "what day is it" lands here, not on "weekday": asked cold, it wants the
    # date. Only the explicit "of the week" phrasings want a bare day name.
    ("date", "today", (
        "whats the date", "what is the date", "whats todays date",
        "what is todays date", "whats the date today",
        "what is the date today", "what date is it", "what date it is",
        "what date is it today", "what date is today",
        "whats the day", "what is the day", "whats the day today",
        "what day is it", "what day it is", "what day is it today",
        "what day is today", "whats today", "what is today",
    )),
    ("weekday", "today", (
        "what day of the week is it", "what day of the week it is",
        "what day of the week is today", "whats the day of the week",
        "what is the day of the week",
    )),
    ("date", "tomorrow", (
        "whats tomorrows date", "what is tomorrows date",
        "whats the date tomorrow", "what is the date tomorrow",
        "what date is tomorrow", "what day is tomorrow",
        "whats tomorrow", "what is tomorrow",
    )),
    ("weekday", "tomorrow", ("what day of the week is tomorrow",)),
    ("date", "yesterday", (
        "whats yesterdays date", "what is yesterdays date",
        "what was yesterdays date", "what was the date yesterday",
        "what date was yesterday", "what day was yesterday",
        "what was yesterday",
    )),
    ("weekday", "yesterday", ("what day of the week was yesterday",)),
    ("month", "today", (
        "what month is it", "what month is this", "what month are we in",
        "whats the month", "what is the month",
    )),
    ("year", "today", (
        "what year is it", "what year is this", "what year are we in",
        "whats the year", "what is the year",
    )),
)
_CLOCK_PHRASES = {
    phrase: (kind, day)
    for kind, day, phrases in _CLOCK_GROUPS for phrase in phrases
}

# Renaming an already-running timer is unambiguous enough to stay deterministic
# even on a continued-conversation turn. Keep these shapes anchored: generic
# "change the timer to ten minutes" is an adjustment and must still reach the
# contextual classifier.
_TIMER_RENAME_PATTERNS = (
    re.compile(
        r"^rename\s+(?:(?:the|my|a)\s+)?"
        r"(?:(?P<label>[a-z0-9][a-z0-9' ]*?)\s+)?timer\s+"
        r"(?:to|as)(?:\s+(?P<new_label>.*))?$"),
    re.compile(
        r"^change\s+(?:(?:the|my|a)\s+)?"
        r"(?:(?P<label>[a-z0-9][a-z0-9' ]*?)\s+)?timer\s+"
        r"to\s+be(?:\s+called)?(?:\s+(?P<new_label>.*))?$"),
    re.compile(
        r"^call\s+(?:(?:the|my|a)\s+)?"
        r"(?:(?P<label>[a-z0-9][a-z0-9' ]*?)\s+)?timer"
        r"(?:\s+(?P<new_label>.*))?$"),
)
_NON_LABEL_STARTS = {
    "actually", "cancel", "forget", "never", "pause", "play", "remove",
    "resume", "set", "start", "stop", "wait", "weather", "what",
}


def _fast_clean(text: str) -> str:
    text = re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower())
    text = " ".join(text.split())
    text = _FAST_LEAD.sub("", text)
    return _FAST_TAIL.sub("", text).strip()


def _timer_label(text: str | None) -> str | None:
    """Normalize a short spoken timer name, rejecting likely new commands."""
    value = _fast_clean(text or "")
    value = re.sub(r"^(?:a|an|the)(?:\s+|$)", "", value)
    value = re.sub(r"\s+timer$", "", value).strip()
    words = value.split()
    if not words or len(words) > 4 or words[0] in _NON_LABEL_STARTS:
        return None
    return value


def fast_parse_timer_rename(command: str) -> dict | None:
    """Parse an anchored timer rename, including one missing its new name.

    This is separate from the fresh-turn fast parser because app.handle_command
    also permits it on follow-ups after a timer was just created.
    """
    text = _fast_clean(command)
    for pattern in _TIMER_RENAME_PATTERNS:
        match = pattern.fullmatch(text)
        if not match:
            continue
        return _validate({
            "intent": "timer_rename",
            "label": _timer_label(match.groupdict().get("label")),
            "new_label": _timer_label(match.groupdict().get("new_label")),
        })
    return None


def fast_parse_weather_location(command: str) -> dict | None:
    """Parse a narrow named-place forecast without risking home fallback."""
    text = _fast_clean(command)
    match = (_WEATHER_LOCATION_RE.fullmatch(text)
             or _WEATHER_LOCATION_PRECIP_RE.fullmatch(text))
    if not match:
        return None
    location = " ".join(match.group("location").split()).strip()
    if (not location or len(location.split()) > 8
            or location in {"here", "home", "outside"}):
        return None
    when = match.groupdict().get("when")
    if when is None:
        when = "today" if match.groupdict().get("kind") == "forecast" else "now"
    return _validate({
        "intent": "weather", "weather_when": when,
        "weather_location": location,
    })


def fast_parse_clock(command: str) -> dict | None:
    """Parse a whole-utterance clock/calendar question, or None.

    Apostrophes are dropped rather than matched: ASR punctuates inconsistently
    ("what's" / "whats", "today's" / "todays"), and every one of these phrases
    would otherwise need to be listed twice.
    """
    text = _fast_clean(command).replace("'", "")
    text = _CLOCK_TAIL.sub("", _CLOCK_LEAD.sub("", text)).strip()
    hit = _CLOCK_PHRASES.get(text)
    if hit is None:
        return None
    return _validate({
        "intent": "time_query", "time_kind": hit[0], "time_day": hit[1],
    })


def _fast_music_volume(text: str) -> int | None:
    """Strict complete-utterance wrapper around spoken_music_volume()."""
    words = text.split()
    if "volume" not in words:
        return None
    pos = words.index("volume")
    prefix = tuple(words[:pos])
    if prefix not in {
        (), ("music",), ("set",), ("set", "the"), ("set", "music"),
        ("set", "the", "music"), ("change",), ("change", "the"),
        ("make", "the", "music"),
    }:
        return None
    tail = words[pos + 1:]
    while tail and tail[0] in ("to", "at", "level"):
        tail = tail[1:]
    if not tail:
        return None
    consumed = 1
    first = tail[0]
    if first.isdigit():
        pass
    elif first in _NUM_WORDS:
        n = _NUM_WORDS[first]
        if (n >= 20 and n % 10 == 0 and len(tail) > 1
                and tail[1] in _NUM_WORDS and 0 < _NUM_WORDS[tail[1]] < 10):
            consumed = 2
    else:
        return None
    remainder = tail[consumed:]
    if remainder not in ([], ["percent"], ["per", "cent"]):
        return None
    return spoken_music_volume(text)


def fast_parse(command: str) -> dict | None:
    """A complete validated intent without the classifier, or None.

    Deliberately excludes labelled timers and play-music searches. Both need
    semantic extraction; a fast path that only *usually* understands them is
    worse than the latency it saves.
    """
    text = _fast_clean(command)
    if not text:
        return None

    rename = fast_parse_timer_rename(text)
    if rename is not None:
        return rename

    cam = _FAST_CAMERA.fullmatch(text)
    if cam:
        target = camera.resolve(cam.group("who"))
        if target:
            return _validate({"intent": "show_camera", "camera_target": target})
    if text in _FAST_CAMERA_CLOSE:
        return _validate({"intent": "close_camera"})

    timer = _FAST_TIMER.fullmatch(text)
    if timer:
        seconds = spoken_duration(timer.group(1))
        if seconds is not None:
            return _validate({
                "intent": "set_timer", "duration_seconds": seconds,
                "sound_theme": config.DEFAULT_THEME,
            })

    volume = _fast_music_volume(text)
    if volume is not None:
        return _validate({
            "intent": "music_control", "music_action": "volume_set",
            "music_volume": volume,
        })
    action = _MUSIC_CONTROL_ALIASES.get(text)
    if action:
        return _validate({"intent": "music_control", "music_action": action})
    if text in _MUSIC_QUERY_ALIASES:
        return _validate({"intent": "music_query"})

    clock_hit = fast_parse_clock(text)
    if clock_hit is not None:
        return clock_hit

    when = None
    if text in _WEATHER_NOW:
        when = "now"
    elif text in _WEATHER_TODAY:
        when = "today"
    else:
        match = _WEATHER_DAY_RE.fullmatch(text) or _WEATHER_PRECIP_RE.fullmatch(text)
        if match:
            when = match.group("when")
    if when:
        return _validate({"intent": "weather", "weather_when": when})
    remote_weather = fast_parse_weather_location(text)
    if remote_weather is not None:
        return remote_weather
    return None


def is_camera_back(command: str) -> bool:
    """A bare "go back"-style dismissal, which means close the camera only when
    one is up. The caller checks the display; this is just the phrase match."""
    return _fast_clean(command) in CAMERA_BACK_PHRASES


async def parse(command: str, context: str | None = None,
                sat: str | None = None) -> dict:
    """Parse a command string into a validated intent dict. When `context` is
    given (a follow-up turn), append the follow-up note so ambiguous/background
    speech routes to "none"."""
    system = _system(sat)
    if context:
        system += "\n" + _FOLLOWUP_NOTE.format(context=context)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": command},
    ]
    raw = await clients.parse_intent_raw(messages)
    data = clients.extract_json(raw)
    parsed = _validate(data)
    # The classifier turned the live phrase "volume eighty" into volume_up,
    # discarding the number. Once it has identified this as music control, an
    # explicit number beside the word "volume" is deterministic and must win
    # over that probabilistic relative/absolute choice.
    absolute = spoken_music_volume(command)
    if parsed["intent"] == "music_control" and absolute is not None:
        parsed["music_action"] = "volume_set"
        parsed["music_volume"] = absolute
    return parsed


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

    new_label = data.get("new_label")
    if isinstance(new_label, str):
        new_label = new_label.strip().lower() or None
    else:
        new_label = None
    if intent != "timer_rename":
        new_label = None

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

    # Meaningless outside an add — a stray true elsewhere must not arm a slot.
    missing_content = (bool(data.get("missing_content"))
                       and intent in ("add_items", "set_reminder"))

    media_type = data.get("media_type")
    if media_type not in ("artist", "album", "track", "playlist"):
        media_type = None

    music_action = data.get("music_action")
    if music_action not in MUSIC_ACTIONS:
        music_action = None

    music_volume = data.get("music_volume")
    if isinstance(music_volume, (int, float)) and not isinstance(music_volume, bool):
        music_volume = int(music_volume)
        if not 0 <= music_volume <= 100:
            music_volume = None
    else:
        music_volume = None
    if music_action == "volume_set" and music_volume is None:
        music_action = None
    if music_action != "volume_set":
        music_volume = None

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

    weather_location = data.get("weather_location")
    if isinstance(weather_location, str):
        weather_location = " ".join(weather_location.split()).strip() or None
    else:
        weather_location = None
    if intent != "weather":
        weather_location = None

    # Both default rather than null: a clock question with an unreadable slot
    # is still answerable ("what time is it, today"), and clock.answer() would
    # have to invent the same defaults anyway.
    time_kind = data.get("time_kind")
    if time_kind not in clock.KINDS:
        time_kind = "time"
    time_day = data.get("time_day")
    if time_day not in clock.DAYS:
        time_day = "today"
    if intent != "time_query":
        time_kind = time_day = None

    hours_when = data.get("hours_when")
    if isinstance(hours_when, str):
        hours_when = hours_when.strip().lower()
    if hours_when not in HOURS_WHEN:
        hours_when = None

    # Resolved through the camera module's own alias table rather than trusted
    # as spoken, so "simon's room" and a bare "Simon" arrive identical and an
    # unknown name arrives as null for the handler to answer.
    camera_target = camera.resolve(data.get("camera_target"))
    if intent != "show_camera":
        camera_target = None

    return {
        "intent": intent,
        "label": label,
        "new_label": new_label,
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
        "missing_content": missing_content,
        "media_type": media_type,
        "music_action": music_action,
        "music_volume": music_volume,
        "sports_action": sports_action,
        "sports_date": sports_date,
        "weather_when": weather_when,
        "weather_location": weather_location,
        "time_kind": time_kind,
        "time_day": time_day,
        "hours_when": hours_when,
        "camera_target": camera_target,
    }
