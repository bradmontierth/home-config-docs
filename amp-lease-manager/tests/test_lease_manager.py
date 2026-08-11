from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from app.config import Config
from app.ha_client import HomeAssistantError
from app.lease_manager import LeaseManager, LeaseManagerError
from app.ma_client import MusicAssistantError
from app.persistence import StateStore


class FakeHA:
    def __init__(self, state: str = "off", unique_id: str = "r3") -> None:
        self.state = state
        self.unique_id = unique_id
        self.last_changed = time.time() - 30 if state == "on" else time.time()
        self.turn_on_calls = 0
        self.turn_off_calls = 0
        self.available = True

    async def validate_entity(self, entity_id: str, expected_unique_id: str) -> dict:
        if not self.available:
            raise HomeAssistantError("HA unavailable")
        if self.unique_id != expected_unique_id:
            raise HomeAssistantError("wrong endpoint")
        return {"entity_id": entity_id, "unique_id": self.unique_id, "device_id": "device"}

    async def get_state(self, entity_id: str) -> dict:
        if not self.available:
            raise HomeAssistantError("HA unavailable")
        return {
            "state": self.state,
            "last_changed": None,
            "last_changed_epoch": self.last_changed,
        }

    async def turn_on(self, entity_id: str) -> None:
        if not self.available:
            raise HomeAssistantError("HA unavailable")
        self.turn_on_calls += 1
        self.state = "on"
        self.last_changed = time.time()

    async def turn_off(self, entity_id: str) -> None:
        if not self.available:
            raise HomeAssistantError("HA unavailable")
        self.turn_off_calls += 1
        self.state = "off"
        self.last_changed = time.time()

    async def wait_for_state(self, entity_id: str, expected: str, timeout_seconds: float = 5) -> dict:
        assert self.state == expected
        return await self.get_state(entity_id)

    async def aclose(self) -> None:
        return None


class FakeMA:
    def __init__(self) -> None:
        self.available = True
        self.active_players: list[dict] = []

    async def player_activity(self, monitored_player_ids: tuple[str, ...]) -> dict:
        if not self.available:
            raise MusicAssistantError("MA unavailable")
        players = [
            {
                "player_id": player_id,
                "state": "playing" if any(p["player_id"] == player_id for p in self.active_players) else "idle",
                "available": True,
                "announcement_in_progress": False,
                "active": any(p["player_id"] == player_id for p in self.active_players),
            }
            for player_id in monitored_player_ids
        ]
        return {"players": players, "active_players": self.active_players}

    async def aclose(self) -> None:
        return None


def config(tmp_path: Path, *, auto_off: bool = False, ready_seconds: float = 0.01) -> Config:
    return Config(
        ha_base_url="http://ha",
        ha_token="secret",
        relay_entity_id="switch.amp",
        relay_expected_unique_id="r3",
        ma_base_url="http://ma",
        monitored_player_ids=("ma_one", "ma_two"),
        api_token="api-secret",
        data_path=tmp_path / "state.json",
        cold_ready_seconds=ready_seconds,
        idle_hold_seconds=600,
        named_lease_ttl_seconds=120,
        poll_seconds=0.01,
        request_timeout_seconds=1,
        validation_interval_seconds=60,
        allow_turn_on=True,
        allow_automatic_off=auto_off,
    )


def manager(tmp_path: Path, *, state: str = "off", auto_off: bool = False):
    ha = FakeHA(state=state)
    ma = FakeMA()
    instance = LeaseManager(config(tmp_path, auto_off=auto_off), ha, ma, StateStore(tmp_path / "state.json"))
    return instance, ha, ma


@pytest.mark.asyncio
async def test_cold_touch_turns_on_once_and_waits_until_ready(tmp_path: Path) -> None:
    instance, ha, _ = manager(tmp_path)
    await instance._validate_locked(force=True)
    before = time.time()
    result = await instance.touch("node-red", "announcement", True)
    assert ha.turn_on_calls == 1
    assert result["ready"] is True
    assert time.time() - before >= 0.009


@pytest.mark.asyncio
async def test_warm_touch_is_immediate_and_does_not_toggle(tmp_path: Path) -> None:
    instance, ha, _ = manager(tmp_path, state="on")
    await instance._validate_locked(force=True)
    before = time.time()
    result = await instance.touch("node-red", "follow-up", True)
    assert ha.turn_on_calls == 0
    assert result["ready"] is True
    assert time.time() - before < 0.05


@pytest.mark.asyncio
async def test_concurrent_acquires_share_one_cold_transition(tmp_path: Path) -> None:
    instance, ha, _ = manager(tmp_path)
    await instance._validate_locked(force=True)
    first, second = await asyncio.gather(
        instance.acquire("a", "1", "first", 120, True),
        instance.acquire("b", "2", "second", 120, True),
    )
    assert ha.turn_on_calls == 1
    assert first["ready"] and second["ready"]
    assert len(instance.leases) == 2


@pytest.mark.asyncio
async def test_release_never_turns_off_when_auto_off_disabled(tmp_path: Path) -> None:
    instance, ha, _ = manager(tmp_path, state="on")
    await instance._validate_locked(force=True)
    await instance.acquire("adapter", "session", "play", 120, True)
    result = await instance.release("adapter", "session", "pause")
    assert ha.turn_off_calls == 0
    assert result["relay_state"] == "on"
    assert result["hold_until"] > time.time()


@pytest.mark.asyncio
async def test_status_renew_does_not_turn_on_an_off_relay(tmp_path: Path) -> None:
    instance, ha, _ = manager(tmp_path, state="off")
    await instance._validate_locked(force=True)
    await instance._read_ha_locked()
    result = await instance.renew("adapter", "session", "status says playing", 120)
    assert ha.turn_on_calls == 0
    assert result["relay_state"] == "off"
    assert len(result["active_leases"]) == 1


@pytest.mark.asyncio
async def test_safety_mismatch_blocks_all_turn_on_writes(tmp_path: Path) -> None:
    instance, ha, _ = manager(tmp_path)
    ha.unique_id = "r1"
    await instance._validate_locked(force=True)
    with pytest.raises(LeaseManagerError) as error:
        await instance.touch("node-red", "must fail", True)
    assert error.value.code == "safety_interlock"
    assert ha.turn_on_calls == 0


@pytest.mark.asyncio
async def test_ma_outage_inhibits_automatic_off(tmp_path: Path) -> None:
    instance, ha, ma = manager(tmp_path, state="on", auto_off=True)
    instance.hold_until = time.time() - 1
    ma.available = False
    await instance._reconcile_locked(initial=False)
    assert instance.ma_ok is False
    assert ha.turn_off_calls == 0


@pytest.mark.asyncio
async def test_ha_outage_inhibits_automatic_off(tmp_path: Path) -> None:
    instance, ha, _ = manager(tmp_path, state="on", auto_off=True)
    await instance._reconcile_locked(initial=True)
    instance.hold_until = time.time() - 1
    ha.available = False
    await instance._reconcile_locked(initial=False)
    assert instance.ha_ok is False
    assert ha.turn_off_calls == 0


@pytest.mark.asyncio
async def test_acquire_does_not_claim_ready_when_ha_state_is_unknown(tmp_path: Path) -> None:
    instance, ha, _ = manager(tmp_path, state="on")
    await instance._validate_locked(force=True)
    ha.available = False
    with pytest.raises(LeaseManagerError) as error:
        await instance.acquire("adapter", "session", "HA outage", 120, True)
    assert error.value.code == "relay_state_unknown"
    assert ha.turn_on_calls == 0


@pytest.mark.asyncio
async def test_startup_with_on_relay_creates_recovery_hold(tmp_path: Path) -> None:
    instance, ha, _ = manager(tmp_path, state="on")
    await instance._reconcile_locked(initial=True)
    assert ha.turn_off_calls == 0
    assert instance.hold_until > time.time() + 590


@pytest.mark.asyncio
async def test_expired_idle_relay_turns_off_only_when_all_checks_healthy(tmp_path: Path) -> None:
    instance, ha, _ = manager(tmp_path, state="on", auto_off=True)
    await instance._reconcile_locked(initial=True)
    instance.hold_until = time.time() - 1
    await instance._reconcile_locked(initial=False)
    assert ha.turn_off_calls == 1
    assert instance.relay_state == "off"
