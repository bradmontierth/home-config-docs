"""Spoken/label phrasing helpers."""

from __future__ import annotations


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
