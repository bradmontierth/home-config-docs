"""Lists client: todo / shopping / reminders via the voice-notes companion.

The companion (http://…:8768) is note-centric. Adding items is a two-step
dance we reuse verbatim so the companion's own analyze prompt does the heavy
lifting (typing each item reminder/todo/shopping, parsing due dates, scoring
confidence, deduping against existing items):

    POST /api/notes/sync              create/replace a note holding the raw text
    POST /api/notes/{id}/analyze      extract typed items from that text

Reading + mutating:

    GET    /api/items?user=&status=   list items (type is a per-row field)
    POST   /api/items/{id}/complete   check one off
    DELETE /api/items/{id}            remove one

We forward the user's whole spoken command to analyze rather than pre-parsing
it, because the companion prompt keys off framing words ("shopping list",
"remind me", "todo") to pick each item's type — stripping them would break it.
"""

from __future__ import annotations

import logging
import time
import uuid

import httpx
from rapidfuzz import fuzz

from . import config

log = logging.getLogger("orchestrator.lists")

# Item types the companion assigns, in display order.
LIST_TYPES = ("reminder", "todo", "shopping")


def _now_ms() -> int:
    return int(time.time() * 1000)


async def add_from_text(text: str) -> list[dict]:
    """Sync a note holding `text` and analyze it. Returns the items the
    companion extracted (each {type, text, due_at, confidence}). Empty list if
    nothing was extractable."""
    text = text.strip()
    if not text:
        return []
    note_id = uuid.uuid4().hex
    ts = _now_ms()
    note = {
        "client_note_id": note_id,
        "user": config.LIST_OWNER,
        "note_date": time.strftime("%Y-%m-%d"),
        "title": "",
        "content": text,
        "mode": "assistant",
        "created_at": ts,
        "updated_at": ts,
    }
    async with httpx.AsyncClient(timeout=30, base_url=config.COMPANION_URL) as client:
        r = await client.post("/api/notes/sync", json=note)
        r.raise_for_status()
        r = await client.post(
            f"/api/notes/{note_id}/analyze", json={"source_text": text}
        )
        r.raise_for_status()
        data = r.json()
    items = data.get("items") or []
    log.info("add_from_text %r -> %d item(s)", text, len(items))
    return items


async def fetch(types: tuple[str, ...] | None = None, status: str = "active") -> list[dict]:
    """List SHARED items (every companion user), optionally filtered to `types`.
    No user filter — the household has one shopping/todo/reminder list. The
    companion already orders reminders, then todos, then shopping."""
    async with httpx.AsyncClient(timeout=15, base_url=config.COMPANION_URL) as client:
        r = await client.get("/api/items", params={"status": status})
        r.raise_for_status()
        items = r.json().get("items") or []
    if types is not None:
        items = [it for it in items if it.get("type") in types]
    return items


def _match_score(query: str, text: str) -> float:
    q, t = query.lower().strip(), (text or "").lower().strip()
    if not q or not t:
        return 0.0
    # partial_ratio catches "eggs" in "Buy eggs"; token_set_ratio catches word
    # reordering ("dentist call" vs "Call the dentist"). Take the stronger.
    return max(fuzz.partial_ratio(q, t), fuzz.token_set_ratio(q, t))


async def complete_by_text(item_text: str) -> dict | None:
    """Find the best active item matching `item_text` and mark it complete.
    Returns the completed item (with its pre-completion fields), or None if
    nothing cleared the match threshold."""
    item_text = (item_text or "").strip()
    if not item_text:
        return None
    items = await fetch(status="active")
    best, best_score = None, 0.0
    for it in items:
        score = _match_score(item_text, it.get("text", ""))
        if score > best_score:
            best, best_score = it, score
    if best is None or best_score < config.LIST_MATCH_THRESHOLD:
        log.info("complete_by_text %r: no match (best=%.0f)", item_text, best_score)
        return None
    async with httpx.AsyncClient(timeout=15, base_url=config.COMPANION_URL) as client:
        r = await client.post(f"/api/items/{best['id']}/complete")
        r.raise_for_status()
    log.info("complete_by_text %r -> #%s %r (score %.0f)",
             item_text, best["id"], best.get("text"), best_score)
    return best


async def complete_by_id(item_id: int) -> dict | None:
    """Mark a specific item complete (touchscreen tap on the kiosk). Returns the
    item as it was before completion, or None if the id is unknown."""
    match = next((it for it in await fetch(status="active") if it.get("id") == item_id), None)
    async with httpx.AsyncClient(timeout=15, base_url=config.COMPANION_URL) as client:
        r = await client.post(f"/api/items/{item_id}/complete")
        if r.status_code == 404:
            return None
        r.raise_for_status()
    log.info("complete_by_id #%s -> %r", item_id, match and match.get("text"))
    return match or {"id": item_id}
