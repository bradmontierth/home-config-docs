from __future__ import annotations

import time
from uuid import uuid4

import httpx


class MusicAssistantError(RuntimeError):
    pass


class MusicAssistantClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers={"Content-Type": "application/json"},
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def player_activity(self, monitored_player_ids: tuple[str, ...]) -> dict:
        try:
            response = await self.client.post(
                "/api",
                json={
                    "message_id": f"amp-lease-{time.time_ns()}-{uuid4().hex[:8]}",
                    "command": "players/all",
                    "args": {},
                },
            )
            response.raise_for_status()
            players = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MusicAssistantError(f"Could not read Music Assistant players: {exc}") from exc

        if isinstance(players, dict) and "result" in players:
            players = players["result"]
        if not isinstance(players, list):
            raise MusicAssistantError("Music Assistant players/all returned a non-list payload")

        by_id = {str(player.get("player_id")): player for player in players}
        missing = [player_id for player_id in monitored_player_ids if player_id not in by_id]
        active: list[dict] = []
        observed: list[dict] = []
        for player_id in monitored_player_ids:
            player = by_id.get(player_id)
            if not player:
                continue
            state = str(player.get("state", "unknown")).lower()
            announcement = bool(player.get("announcement_in_progress", False))
            is_active = state == "playing" or announcement
            summary = {
                "player_id": player_id,
                "state": state,
                "available": bool(player.get("available", False)),
                "announcement_in_progress": announcement,
                "active": is_active,
            }
            observed.append(summary)
            if is_active:
                active.append(summary)
        if missing:
            raise MusicAssistantError(f"Monitored players missing: {', '.join(missing)}")
        return {"players": observed, "active_players": active}
