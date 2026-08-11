from __future__ import annotations

import asyncio
import json
from datetime import datetime
from urllib.parse import urlparse

import httpx
import websockets


class HomeAssistantError(RuntimeError):
    pass


def parse_timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class HomeAssistantClient:
    def __init__(self, base_url: str, token: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def get_state(self, entity_id: str) -> dict:
        try:
            response = await self.client.get(f"/api/states/{entity_id}")
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HomeAssistantError(f"Could not read relay state: {exc}") from exc
        return {
            "state": str(payload.get("state", "unknown")),
            "last_changed": payload.get("last_changed"),
            "last_changed_epoch": parse_timestamp(payload.get("last_changed")),
        }

    async def turn_on(self, entity_id: str) -> None:
        await self._service("turn_on", entity_id)

    async def turn_off(self, entity_id: str) -> None:
        await self._service("turn_off", entity_id)

    async def _service(self, service: str, entity_id: str) -> None:
        try:
            response = await self.client.post(
                f"/api/services/switch/{service}", json={"entity_id": entity_id}
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise HomeAssistantError(f"Home Assistant switch.{service} failed: {exc}") from exc

    async def wait_for_state(
        self, entity_id: str, expected: str, timeout_seconds: float = 5.0
    ) -> dict:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        last: dict | None = None
        while loop.time() < deadline:
            last = await self.get_state(entity_id)
            if last["state"] == expected:
                return last
            await asyncio.sleep(0.1)
        raise HomeAssistantError(
            f"Relay did not reach {expected!r} within {timeout_seconds}s; last={last}"
        )

    async def validate_entity(self, entity_id: str, expected_unique_id: str) -> dict:
        parsed = urlparse(self.base_url)
        websocket_scheme = "wss" if parsed.scheme == "https" else "ws"
        websocket_url = f"{websocket_scheme}://{parsed.netloc}/api/websocket"
        try:
            async with websockets.connect(websocket_url, max_size=None) as socket:
                await socket.recv()
                await socket.send(json.dumps({"type": "auth", "access_token": self.token}))
                auth = json.loads(await socket.recv())
                if auth.get("type") != "auth_ok":
                    raise HomeAssistantError("Home Assistant WebSocket authentication failed")
                await socket.send(json.dumps({"id": 1, "type": "config/entity_registry/list"}))
                response = json.loads(await socket.recv())
        except (OSError, ValueError, websockets.WebSocketException) as exc:
            raise HomeAssistantError(f"Could not validate Home Assistant entity registry: {exc}") from exc

        if not response.get("success"):
            raise HomeAssistantError(f"Entity-registry request failed: {response.get('error')}")
        match = next(
            (entry for entry in response.get("result", []) if entry.get("entity_id") == entity_id),
            None,
        )
        if not match:
            raise HomeAssistantError(f"Configured relay entity {entity_id!r} is absent")
        actual_unique_id = str(match.get("unique_id", ""))
        if actual_unique_id != expected_unique_id:
            raise HomeAssistantError(
                f"Safety interlock: {entity_id!r} maps to {actual_unique_id!r}, "
                f"expected R3 {expected_unique_id!r}"
            )
        return {
            "entity_id": entity_id,
            "unique_id": actual_unique_id,
            "device_id": match.get("device_id"),
        }
