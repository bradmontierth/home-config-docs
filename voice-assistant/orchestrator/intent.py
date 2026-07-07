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
    "ask", "none",
)

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
  "query": for intent "ask", the cleaned question text to send to the knowledge model; else null,
  "item_text": for intent "complete_item", the short name of the list item to check off (e.g. "eggs", "call the dentist"); else null
}}

Rules:
- "set a chicken timer for 12 minutes" -> set_timer, label chicken, 720, cluck.
- Pick sound_theme by the food: poultry->cluck, beef/dairy->moo, frying/searing->sizzle,
  boiling/pasta/rice->steam_whistle, simmering/sauce->bubbling, baking/roasting->oven_ding.
  If unclear or no food, use marimba.
- timer_query = "how much time is left", "how long on the rice". duration_seconds null.
- timer_adjust = "add 5 minutes to the rice" -> duration_seconds 300; "take 2 minutes off" -> -120.
- timer_cancel = "cancel the rice timer" (scope one) or "cancel all timers" (scope all).
- ask = a general knowledge or factual question NOT about timers: "how many tablespoons in a cup",
  "when do babies start walking", "what temperature is chicken done at", "how do I dice an onion".
  Put the cleaned question in "query". No keyword is needed — natural questions route here.
- add_items = adding things to a shopping or todo list: "add eggs and milk to the shopping list",
  "put paper towels on the list", "add a todo to call the plumber". Leave label/query/item_text null;
  the full command is forwarded to the list service, which figures out the items itself.
- set_reminder = a time-based reminder: "remind me to take the roast out at 5", "remind me to call mom
  tomorrow morning". Also forwarded whole; leave item_text null.
- show_todos = "show my todos", "what's on my to-do list". show_shopping = "show the shopping list",
  "what do I need to buy". No other fields.
- complete_item = checking something off: "mark eggs as done", "I bought the milk", "cross off call
  the dentist". Put the item name in "item_text".
- If the command is not a timer, list, or knowledge command, intent "none".
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
Only produce an action intent if the command is CLEARLY addressed to you and \
actionable (e.g. "also add butter", "make that fifteen minutes", "and cancel the \
rice"). If it is small talk, a fragment, someone else talking, or anything not \
clearly a command to you, return intent "none" with all other fields null. When \
in doubt, return "none"."""


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

    return {
        "intent": intent,
        "label": label,
        "duration_seconds": duration,
        "sound_theme": theme,
        "scope": scope,
        "query": query,
        "item_text": item_text,
    }
