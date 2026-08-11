from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_PLAYERS = (
    "ma_loft",
    "ma_simon_room",
    "ma_claire_room",
    "ma_master_bedroom",
    "ma_shower",
)


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _secret(value_name: str, file_name: str) -> str:
    direct = os.getenv(value_name, "").strip()
    if direct:
        return direct
    path = os.getenv(file_name, "").strip()
    if not path:
        return ""
    return Path(path).read_text(encoding="utf-8").strip()


@dataclass(frozen=True)
class Config:
    ha_base_url: str
    ha_token: str
    relay_entity_id: str
    relay_expected_unique_id: str
    ma_base_url: str
    monitored_player_ids: tuple[str, ...]
    api_token: str
    data_path: Path
    cold_ready_seconds: float
    idle_hold_seconds: int
    named_lease_ttl_seconds: int
    poll_seconds: float
    request_timeout_seconds: float
    validation_interval_seconds: float
    allow_turn_on: bool
    allow_automatic_off: bool

    @classmethod
    def from_env(cls) -> "Config":
        players = tuple(
            item.strip()
            for item in os.getenv("MA_MONITORED_PLAYER_IDS", ",".join(DEFAULT_PLAYERS)).split(",")
            if item.strip()
        )
        config = cls(
            ha_base_url=os.getenv("HA_BASE_URL", "http://192.168.10.217:8123").rstrip("/"),
            ha_token=_secret("HA_TOKEN", "HA_TOKEN_FILE"),
            relay_entity_id=os.getenv(
                "AMP_RELAY_ENTITY_ID", "switch.whole_home_audio_amp_trigger"
            ),
            relay_expected_unique_id=os.getenv(
                "AMP_RELAY_EXPECTED_UNIQUE_ID",
                "3845559058.28-37-3-currentValue",
            ),
            ma_base_url=os.getenv("MA_BASE_URL", "http://192.168.10.217:8095").rstrip("/"),
            monitored_player_ids=players,
            api_token=_secret("AMP_LEASE_API_TOKEN", "AMP_LEASE_API_TOKEN_FILE"),
            data_path=Path(os.getenv("STATE_PATH", "/data/state.json")),
            cold_ready_seconds=float(os.getenv("AMP_COLD_READY_SECONDS", "5.0")),
            idle_hold_seconds=int(os.getenv("AMP_IDLE_HOLD_SECONDS", "600")),
            named_lease_ttl_seconds=int(os.getenv("AMP_NAMED_LEASE_TTL_SECONDS", "120")),
            poll_seconds=float(os.getenv("POLL_SECONDS", "5")),
            request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "5")),
            validation_interval_seconds=float(os.getenv("VALIDATION_INTERVAL_SECONDS", "60")),
            allow_turn_on=_bool("ALLOW_TURN_ON", True),
            allow_automatic_off=_bool("ALLOW_AUTOMATIC_OFF", False),
        )
        if not config.ha_token:
            raise ValueError("HA_TOKEN or HA_TOKEN_FILE is required")
        if not config.api_token:
            raise ValueError("AMP_LEASE_API_TOKEN or AMP_LEASE_API_TOKEN_FILE is required")
        if not config.monitored_player_ids:
            raise ValueError("At least one MA_MONITORED_PLAYER_IDS entry is required")
        if config.cold_ready_seconds < 0 or config.idle_hold_seconds <= 0:
            raise ValueError("Readiness and hold durations are invalid")
        return config
