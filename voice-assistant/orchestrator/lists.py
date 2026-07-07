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

from . import clients, config

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
    if not items:
        return []
    # analyze returns items WITHOUT ids (type/text/due_at/confidence only). Match
    # them back to the stored active rows so callers get ids (needed to delete on
    # a follow-up "undo" / complete by tap). Companion stores the exact text, so
    # match on (type, lowercased text).
    active = await fetch(status="active")
    resolved = []
    for it in items:
        key = (it.get("type"), (it.get("text") or "").strip().lower())
        row = next((r for r in active
                    if (r.get("type"), (r.get("text") or "").strip().lower()) == key), None)
        resolved.append(row or it)
    log.info("add_from_text %r -> %d item(s)", text, len(resolved))
    return resolved


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


_RESOLVE_SYSTEM = (
    "You select which list items the user wants to act on. You get the current "
    "list (each line 'id=<n> <type>: <text>') and a phrase describing a target. "
    "Return ONLY JSON: {\"ids\": [matching item ids]}.\n"
    "Match by meaning: a specific item (\"the milk\") -> that item; several "
    "(\"milk and bread\") -> each; a category (\"the dairy\", \"produce\") -> all "
    "items in it; a property (\"everything orange\") -> all items with it; "
    "\"all\"/\"everything\"/\"the whole list\" -> every id. Only use ids from the "
    "list. If nothing matches, return {\"ids\": []}. Output JSON only."
)


async def resolve_targets(criterion: str, items: list[dict]) -> list[dict]:
    """Ask the LLM which items match a natural-language target (one item, several,
    a category, or a property). Returns the matching rows, order preserved."""
    criterion = (criterion or "").strip()
    if not criterion or not items:
        return []
    listing = "\n".join(
        f'id={it.get("id")} {it.get("type")}: {it.get("text")}' for it in items
    )
    messages = [
        {"role": "system", "content": _RESOLVE_SYSTEM},
        {"role": "user", "content": f"List:\n{listing}\n\nPhrase: {criterion!r}"},
    ]
    try:
        data = clients.extract_json(await clients.parse_intent_raw(messages))
    except Exception as exc:  # noqa: BLE001
        log.warning("resolve_targets %r failed: %s", criterion, exc)
        return []
    wanted = set()
    for i in data.get("ids") or []:
        try:
            wanted.add(int(i))
        except (TypeError, ValueError):
            pass
    matched = [it for it in items if it.get("id") in wanted]
    log.info("resolve_targets %r -> %s", criterion, [it.get("text") for it in matched])
    return matched


async def delete_ids(items: list[dict]) -> list[dict]:
    """Delete a known set of items (undo of an add, or a resolved bulk remove).
    Returns those that were still active and got deleted."""
    active_ids = {it.get("id") for it in await fetch(status="active")}
    removed = []
    for it in items:
        if it.get("id") in active_ids:
            await _delete(it["id"])
            removed.append(it)
    if removed:
        log.info("delete_ids removed %s", [it.get("text") for it in removed])
    return removed


async def complete_ids(items: list[dict]) -> list[dict]:
    """Mark a known set of items complete. Returns those still active that were
    completed."""
    active_ids = {it.get("id") for it in await fetch(status="active")}
    done = []
    for it in items:
        if it.get("id") in active_ids:
            await _complete(it["id"])
            done.append(it)
    if done:
        log.info("complete_ids completed %s", [it.get("text") for it in done])
    return done


async def clear(list_type: str | None) -> list[dict]:
    """Delete every active item on a list ("shopping"/"todo"/"all" or None=all).
    Returns the removed rows."""
    types = None if list_type in (None, "all") else (list_type,)
    items = await fetch(types=types, status="active")
    for it in items:
        await _delete(it["id"])
    log.info("clear %s removed %d item(s)", list_type, len(items))
    return items


async def _delete(item_id: int) -> None:
    async with httpx.AsyncClient(timeout=15, base_url=config.COMPANION_URL) as client:
        r = await client.delete(f"/api/items/{item_id}")
        if r.status_code != 404:
            r.raise_for_status()


async def _complete(item_id: int) -> None:
    async with httpx.AsyncClient(timeout=15, base_url=config.COMPANION_URL) as client:
        r = await client.post(f"/api/items/{item_id}/complete")
        if r.status_code != 404:
            r.raise_for_status()


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
