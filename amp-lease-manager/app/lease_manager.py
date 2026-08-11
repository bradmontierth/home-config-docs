from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, asdict
from typing import Any

from .config import Config
from .ha_client import HomeAssistantClient, HomeAssistantError
from .ma_client import MusicAssistantClient, MusicAssistantError
from .persistence import StateStore


LOGGER = logging.getLogger("amp_lease_manager")


class LeaseManagerError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Lease:
    owner: str
    lease_id: str
    reason: str
    expires_at: float


class LeaseManager:
    def __init__(
        self,
        config: Config,
        ha: HomeAssistantClient,
        ma: MusicAssistantClient,
        store: StateStore,
    ) -> None:
        self.config = config
        self.ha = ha
        self.ma = ma
        self.store = store
        self.lock = asyncio.Lock()
        self.stop_event = asyncio.Event()
        self.background_task: asyncio.Task | None = None
        self.started_at = time.time()
        self.hold_until = 0.0
        self.leases: dict[str, Lease] = {}
        self.relay_state = "unknown"
        self.relay_last_changed: float | None = None
        self.ready_at: float | None = None
        self.safety_valid = False
        self.safety_details: dict[str, Any] | None = None
        self.ha_ok = False
        self.ha_last_success_at: float | None = None
        self.ha_error: str | None = None
        self.ma_ok = False
        self.ma_last_success_at: float | None = None
        self.ma_error: str | None = None
        self.ma_players: list[dict] = []
        self.ma_active_players: list[dict] = []
        self.last_validation_at = 0.0
        self.last_command: dict[str, Any] | None = None
        self.last_event: dict[str, Any] | None = None
        self.initialized = False
        self._load()

    def _lease_key(self, owner: str, lease_id: str) -> str:
        return f"{owner}:{lease_id}"

    def _load(self) -> None:
        value = self.store.load()
        now = time.time()
        self.hold_until = float(value.get("hold_until", 0) or 0)
        self.relay_state = str(value.get("relay_state", "unknown"))
        self.relay_last_changed = value.get("relay_last_changed")
        self.ready_at = value.get("ready_at")
        self.last_command = value.get("last_command")
        for raw in value.get("leases", []):
            try:
                lease = Lease(
                    owner=str(raw["owner"]),
                    lease_id=str(raw["lease_id"]),
                    reason=str(raw.get("reason", "recovered")),
                    expires_at=float(raw["expires_at"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if lease.expires_at > now:
                self.leases[self._lease_key(lease.owner, lease.lease_id)] = lease

    def _persistent_value(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "hold_until": self.hold_until,
            "leases": [asdict(lease) for lease in self.leases.values()],
            "relay_state": self.relay_state,
            "relay_last_changed": self.relay_last_changed,
            "ready_at": self.ready_at,
            "last_command": self.last_command,
        }

    def _save(self) -> None:
        self.store.save(self._persistent_value())

    def _event(self, event: str, **fields: Any) -> None:
        payload = {"event": event, "at": time.time(), **fields}
        self.last_event = payload
        LOGGER.info("amp_event %s", payload)

    def _extend_hold(self, now: float | None = None) -> None:
        now = now or time.time()
        old = self.hold_until
        self.hold_until = max(self.hold_until, now + self.config.idle_hold_seconds)
        if self.hold_until != old:
            self._event("hold_extended", hold_until=self.hold_until)

    def _expire_leases(self, now: float | None = None) -> None:
        now = now or time.time()
        expired = [key for key, lease in self.leases.items() if lease.expires_at <= now]
        for key in expired:
            lease = self.leases.pop(key)
            self._event("lease_expired", owner=lease.owner, lease_id=lease.lease_id)

    async def start(self) -> None:
        async with self.lock:
            await self._reconcile_locked(initial=True)
            self.initialized = True
            self._save()
        self.background_task = asyncio.create_task(self._background_loop())

    async def close(self) -> None:
        self.stop_event.set()
        if self.background_task:
            await self.background_task
        await self.ha.aclose()
        await self.ma.aclose()

    async def _background_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.config.poll_seconds)
                continue
            except asyncio.TimeoutError:
                pass
            try:
                async with self.lock:
                    await self._reconcile_locked(initial=False)
            except Exception:
                LOGGER.exception("Unexpected reconciliation failure")

    async def _validate_locked(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_validation_at < self.config.validation_interval_seconds:
            return
        self.last_validation_at = now
        try:
            self.safety_details = await self.ha.validate_entity(
                self.config.relay_entity_id, self.config.relay_expected_unique_id
            )
            self.safety_valid = True
            self._event("safety_validated", **self.safety_details)
        except HomeAssistantError as exc:
            self.safety_valid = False
            self.safety_details = None
            self.ha_error = str(exc)
            self._event("safety_validation_failed", error=str(exc))

    async def _read_ha_locked(self) -> None:
        try:
            state = await self.ha.get_state(self.config.relay_entity_id)
            prior = self.relay_state
            self.relay_state = state["state"]
            self.relay_last_changed = state.get("last_changed_epoch")
            if self.relay_state == "on" and self.relay_last_changed is not None:
                self.ready_at = self.relay_last_changed + self.config.cold_ready_seconds
            elif self.relay_state != "on":
                self.ready_at = None
            self.ha_ok = True
            self.ha_last_success_at = time.time()
            self.ha_error = None
            if prior != self.relay_state:
                self._event("relay_observed", prior=prior, state=self.relay_state)
        except HomeAssistantError as exc:
            self.ha_ok = False
            self.ha_error = str(exc)
            self._event("ha_unavailable", error=str(exc))

    async def _read_ma_locked(self) -> None:
        try:
            activity = await self.ma.player_activity(self.config.monitored_player_ids)
            previous_ids = {player["player_id"] for player in self.ma_active_players}
            self.ma_players = activity["players"]
            self.ma_active_players = activity["active_players"]
            current_ids = {player["player_id"] for player in self.ma_active_players}
            self.ma_ok = True
            self.ma_last_success_at = time.time()
            self.ma_error = None
            if current_ids:
                self._extend_hold()
            if previous_ids != current_ids:
                self._event("ma_activity_changed", active_players=sorted(current_ids))
        except MusicAssistantError as exc:
            self.ma_ok = False
            self.ma_error = str(exc)
            self._event("ma_unavailable", error=str(exc))

    async def _reconcile_locked(self, initial: bool) -> None:
        now = time.time()
        self._expire_leases(now)
        await self._validate_locked(force=initial)
        await self._read_ha_locked()
        await self._read_ma_locked()

        if initial and self.relay_state == "on" and not self.leases and not self.ma_active_players:
            self._extend_hold(now)

        required = bool(self.leases or self.ma_active_players)
        if required and self.relay_state == "off" and self.safety_valid and self.ha_ok:
            self._event("unexpected_off_while_required")
            try:
                await self._ensure_ready_locked(wait=False, reason="reconciliation")
            except LeaseManagerError as exc:
                self._event("reassert_on_failed", code=exc.code, error=str(exc))

        off_allowed = (
            self.config.allow_automatic_off
            and self.safety_valid
            and self.ha_ok
            and self.ma_ok
            and not self.leases
            and not self.ma_active_players
            and now >= self.hold_until
        )
        if off_allowed and self.relay_state == "on":
            await self._turn_off_locked("idle_hold_expired")
        self._save()

    async def _turn_off_locked(self, reason: str) -> None:
        if not self.safety_valid:
            raise LeaseManagerError("safety_interlock", "R3 safety validation is not passing")
        try:
            await self.ha.turn_off(self.config.relay_entity_id)
            state = await self.ha.wait_for_state(self.config.relay_entity_id, "off")
        except HomeAssistantError as exc:
            self.ha_ok = False
            self.ha_error = str(exc)
            raise LeaseManagerError("relay_control_failed", str(exc)) from exc
        self.relay_state = "off"
        self.relay_last_changed = state.get("last_changed_epoch")
        self.ready_at = None
        self.last_command = {"command": "off", "reason": reason, "at": time.time()}
        self._event("relay_commanded_off", reason=reason)

    async def _ensure_ready_locked(self, wait: bool, reason: str) -> dict[str, Any]:
        await self._validate_locked()
        if not self.safety_valid:
            raise LeaseManagerError("safety_interlock", self.ha_error or "R3 validation failed")

        try:
            state = await self.ha.get_state(self.config.relay_entity_id)
        except HomeAssistantError as exc:
            self.ha_ok = False
            self.ha_error = str(exc)
            raise LeaseManagerError("relay_state_unknown", str(exc)) from exc
        self.ha_ok = True
        self.ha_last_success_at = time.time()
        self.ha_error = None
        self.relay_state = state["state"]
        self.relay_last_changed = state.get("last_changed_epoch")

        if self.relay_state != "on":
            if not self.config.allow_turn_on:
                raise LeaseManagerError("turn_on_disabled", "Relay turn-on is disabled")
            try:
                await self.ha.turn_on(self.config.relay_entity_id)
                state = await self.ha.wait_for_state(self.config.relay_entity_id, "on")
            except HomeAssistantError as exc:
                self.ha_ok = False
                self.ha_error = str(exc)
                raise LeaseManagerError("relay_control_failed", str(exc)) from exc
            now = time.time()
            self.relay_state = "on"
            self.relay_last_changed = state.get("last_changed_epoch") or now
            self.ready_at = now + self.config.cold_ready_seconds
            self.last_command = {"command": "on", "reason": reason, "at": now}
            self._event("relay_commanded_on", reason=reason, ready_at=self.ready_at)
        else:
            changed = self.relay_last_changed or time.time()
            self.ready_at = changed + self.config.cold_ready_seconds

        remaining = max(0.0, (self.ready_at or time.time()) - time.time())
        if wait and remaining:
            self._event("readiness_wait", seconds=remaining, reason=reason)
            await asyncio.sleep(remaining)
        self._save()
        return self.status()

    async def acquire(
        self,
        owner: str,
        lease_id: str,
        reason: str,
        ttl_seconds: int | None,
        wait_for_ready: bool,
    ) -> dict[str, Any]:
        async with self.lock:
            now = time.time()
            ttl = ttl_seconds or self.config.named_lease_ttl_seconds
            lease = Lease(owner, lease_id, reason, now + ttl)
            self.leases[self._lease_key(owner, lease_id)] = lease
            self._extend_hold(now)
            self._event(
                "lease_acquired", owner=owner, lease_id=lease_id, reason=reason, expires_at=lease.expires_at
            )
            self._save()
            return await self._ensure_ready_locked(wait_for_ready, reason)

    async def touch(self, owner: str, reason: str, wait_for_ready: bool) -> dict[str, Any]:
        async with self.lock:
            self._extend_hold()
            self._event("activity_touched", owner=owner, reason=reason)
            self._save()
            return await self._ensure_ready_locked(wait_for_ready, reason)

    async def release(self, owner: str, lease_id: str, reason: str) -> dict[str, Any]:
        async with self.lock:
            key = self._lease_key(owner, lease_id)
            removed = self.leases.pop(key, None)
            self._extend_hold()
            self._event(
                "lease_released",
                owner=owner,
                lease_id=lease_id,
                reason=reason,
                existed=removed is not None,
            )
            self._save()
            return self.status()

    async def renew(
        self, owner: str, lease_id: str, reason: str, ttl_seconds: int | None
    ) -> dict[str, Any]:
        """Renew policy ownership without issuing a relay-on command.

        This is intentionally distinct from acquire so status polling can keep
        an active session alive without ever waking an otherwise-off amp.
        """
        async with self.lock:
            now = time.time()
            ttl = ttl_seconds or self.config.named_lease_ttl_seconds
            lease = Lease(owner, lease_id, reason, now + ttl)
            self.leases[self._lease_key(owner, lease_id)] = lease
            self._extend_hold(now)
            self._event(
                "lease_renewed",
                owner=owner,
                lease_id=lease_id,
                reason=reason,
                expires_at=lease.expires_at,
            )
            self._save()
            return self.status()

    def status(self) -> dict[str, Any]:
        now = time.time()
        self._expire_leases(now)
        active = bool(self.leases or self.ma_active_players)
        ready = self.relay_state == "on" and self.ready_at is not None and now >= self.ready_at
        dependencies_ok = self.ha_ok and self.ma_ok and self.safety_valid
        if not dependencies_ok:
            policy_state = "degraded"
        elif active:
            policy_state = "active"
        elif self.relay_state == "on" and not ready:
            policy_state = "waking"
        elif self.relay_state == "on" and now < self.hold_until:
            policy_state = "holding"
        elif self.relay_state == "on":
            policy_state = "ready"
        else:
            policy_state = "off"
        automatic_off_allowed_now = (
            self.config.allow_automatic_off
            and dependencies_ok
            and not active
            and now >= self.hold_until
        )
        return {
            "policy_state": policy_state,
            "relay_state": self.relay_state,
            "ready": ready,
            "ready_at": self.ready_at,
            "hold_until": self.hold_until,
            "active_leases": [asdict(lease) for lease in self.leases.values()],
            "music_assistant": {
                "ok": self.ma_ok,
                "last_success_at": self.ma_last_success_at,
                "error": self.ma_error,
                "players": self.ma_players,
                "active_players": self.ma_active_players,
            },
            "home_assistant": {
                "ok": self.ha_ok,
                "last_success_at": self.ha_last_success_at,
                "error": self.ha_error,
            },
            "safety": {
                "valid": self.safety_valid,
                "expected_entity_id": self.config.relay_entity_id,
                "expected_unique_id": self.config.relay_expected_unique_id,
                "details": self.safety_details,
            },
            "automatic_off": {
                "configured": self.config.allow_automatic_off,
                "allowed_now": automatic_off_allowed_now,
            },
            "last_command": self.last_command,
            "last_event": self.last_event,
            "initialized": self.initialized,
            "started_at": self.started_at,
            "now": now,
        }
