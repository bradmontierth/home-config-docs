"""Spoken/label phrasing helpers."""

from __future__ import annotations

from datetime import datetime


def humanize_seconds(seconds: int) -> str:
    seconds = max(0, int(round(seconds)))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    parts: list[str] = []
    if h:
        parts.append(f"{h} hour" + ("s" if h != 1 else ""))
    if m:
        parts.append(f"{m} minute" + ("s" if m != 1 else ""))
    if s and not h:  # drop seconds when hours present — nobody says "1 hour 3 seconds"
        parts.append(f"{s} second" + ("s" if s != 1 else ""))
    if not parts:
        return "0 seconds"
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def timer_name(timer: dict) -> str:
    return f"{timer['label']} timer" if timer.get("label") else "timer"


def confirm_set(timer: dict) -> str:
    return f"{timer_name(timer).capitalize()} set for {humanize_seconds(timer['duration_seconds'])}."


def confirm_cancel(timer: dict) -> str:
    return f"Cancelled the {timer_name(timer)}."


def confirm_adjust(timer: dict) -> str:
    return (
        f"Updated the {timer_name(timer)}. "
        f"{humanize_seconds(timer['remaining_seconds'])} left."
    )


def report_query(timers: list[dict]) -> str:
    if not timers:
        return "You have no timers running."
    if len(timers) == 1:
        t = timers[0]
        return f"The {timer_name(t)} has {humanize_seconds(t['remaining_seconds'])} left."
    parts = [
        f"{timer_name(t)}, {humanize_seconds(t['remaining_seconds'])}" for t in timers
    ]
    return "You have " + "; ".join(parts) + "."


# --- lists -----------------------------------------------------------------
_LIST_LABEL = {"todo": "to-do list", "shopping": "shopping list", "reminder": "reminders"}


def join_natural(words: list[str]) -> str:
    """['a','b','c'] -> 'a, b, and c'."""
    words = [w for w in words if w]
    if not words:
        return ""
    if len(words) == 1:
        return words[0]
    if len(words) == 2:
        return f"{words[0]} and {words[1]}"
    return ", ".join(words[:-1]) + ", and " + words[-1]


def humanize_due(due_at: str | None) -> str:
    """ISO-8601 (with offset) -> ' tomorrow at 5:00 PM' style tail, or '' if
    unparseable/absent. Leading space included so it drops cleanly into a
    sentence."""
    if not due_at:
        return ""
    try:
        dt = datetime.fromisoformat(due_at)
    except ValueError:
        return ""
    now = datetime.now(dt.tzinfo)
    day_delta = (dt.date() - now.date()).days
    if day_delta == 0:
        day = "today"
    elif day_delta == 1:
        day = "tomorrow"
    else:
        day = "on " + dt.strftime("%A")
    clock = dt.strftime("%I:%M %p").lstrip("0")
    return f" {day} at {clock}"


# Companion prefixes shopping items with an imperative ("Buy eggs"). Fine on the
# dashboard, but "Added buy eggs" reads badly aloud — strip it for speech only.
_SHOPPING_PREFIXES = ("buy ", "get ", "grab ", "pick up ", "purchase ", "add ")


def _spoken_text(item: dict) -> str:
    text = (item.get("text") or "").strip()
    if item.get("type") == "shopping":
        low = text.lower()
        for pre in _SHOPPING_PREFIXES:
            if low.startswith(pre):
                return text[len(pre):].lstrip()
    return text


def _item_phrase(item: dict) -> str:
    text = _spoken_text(item)
    if item.get("type") == "reminder":
        return text + humanize_due(item.get("due_at"))
    return text


def summarize_added(items: list[dict]) -> str:
    """Confirmation for an add_items / set_reminder turn, grouped by type."""
    if not items:
        return "I didn't catch anything to add to your lists."
    buckets: dict[str, list[str]] = {"reminder": [], "todo": [], "shopping": []}
    for it in items:
        buckets.setdefault(it.get("type", "todo"), []).append(_item_phrase(it))
    clauses = []
    if buckets["reminder"]:
        clauses.append("I'll remind you to " + join_natural(buckets["reminder"]))
    if buckets["shopping"]:
        clauses.append("added " + join_natural(buckets["shopping"]) + " to the shopping list")
    if buckets["todo"]:
        clauses.append("added " + join_natural(buckets["todo"]) + " to your to-dos")
    if not clauses:
        return "Added to your lists."
    sentence = clauses[0][0].upper() + clauses[0][1:]
    if len(clauses) > 1:
        sentence = "; ".join([sentence] + clauses[1:])
    return sentence + "."


def summarize_list(list_type: str, items: list[dict]) -> str:
    """Spoken summary when showing a list on the dashboard."""
    label = _LIST_LABEL.get(list_type, "list")
    if not items:
        return f"Your {label} is empty."
    names = [_item_phrase(it) for it in items]
    head = names[:5]
    spoken = join_natural(head)
    if len(names) > len(head):
        spoken += f", and {len(names) - len(head)} more"
    n = len(names)
    noun = "item" if n == 1 else "items"
    return f"You have {n} {noun} on your {label}: {spoken}."


def confirm_removed(items: list[dict]) -> str:
    names = [_spoken_text(it) for it in items]
    if not names:
        return "Removed it."
    return "Removed " + join_natural(names) + "."


def confirm_complete(item: dict) -> str:
    text = _spoken_text(item) or "that"
    verb = "Checked off" if item.get("type") == "shopping" else "Marked"
    tail = "" if item.get("type") == "shopping" else " as done"
    return f"{verb} {text}{tail}."


def confirm_completed(items: list[dict]) -> str:
    if len(items) == 1:
        return confirm_complete(items[0])
    return "Checked off " + join_natural([_spoken_text(it) for it in items]) + "."


_CLEAR_LABEL = {"shopping": "shopping list", "todo": "to-do list", "all": "lists"}


def confirm_cleared(list_type: str | None, items: list[dict]) -> str:
    label = _CLEAR_LABEL.get(list_type or "all", "list")
    n = len(items)
    if not n:
        return f"Your {label} was already empty."
    noun = "item" if n == 1 else "items"
    return f"Cleared the {label} — removed {n} {noun}."


# --- music -------------------------------------------------------------------
def confirm_play(sel: dict) -> str:
    """Spoken confirmation for play_music, by what the ranker picked."""
    kind, name = sel.get("kind"), sel.get("name")
    if kind == "resume":
        return "Resuming your music."
    if kind == "artist":
        return f"Shuffling {name}."
    if kind == "album":
        return f"Playing the album {name}."
    if kind == "playlist":
        return f"Shuffling the playlist {name}."
    artist = sel.get("artist")
    if artist:
        return f"Playing {name} by {artist}."
    return f"Playing {name}."


_MUSIC_ACK = {
    "pause": "Paused.",
    "resume": "Okay, resuming.",
    "stop": "Stopped the music.",
    "next": "Skipping.",
    "previous": "Going back.",
    "volume_up": "Okay, louder.",
    "volume_down": "Okay, quieter.",
}


def confirm_music_control(action: str) -> str:
    return _MUSIC_ACK.get(action, "Done.")


def now_playing_phrase(np: dict | None) -> str:
    if not np or not np.get("track"):
        return "Nothing is playing right now."
    track, artist = np["track"], np.get("artist")
    what = f"{track} by {artist}" if artist else track
    if np.get("state") == "paused":
        return f"{what} is paused."
    return f"This is {what}."


def confirm_bulk_question(op: str, items: list[dict], list_type: str | None = None) -> str:
    """Spoken confirmation prompt before a destructive bulk op."""
    n = len(items)
    if op == "clear":
        label = _CLEAR_LABEL.get(list_type or "all", "list")
        noun = "item" if n == 1 else "items"
        return f"That'll clear the {label} — {n} {noun}. Say yes to confirm."
    names = join_natural([_spoken_text(it) for it in items])
    noun = "item" if n == 1 else "items"
    return f"That'll remove {n} {noun}: {names}. Say yes to confirm."
