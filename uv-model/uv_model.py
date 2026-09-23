#!/usr/bin/env python3
"""Modeled UV index for Home Assistant.

Solar geometry -> UVI = A * sin(elevation)^P -> retained MQTT JSON + HA discovery.

The curve is a clear/typical-sky *climatology* fit for Salt Lake City (A=11.15,
P=2.32 were fitted against SLC's published monthly maximum UV). It knows today's
exact sun position but nothing about today's clouds, ozone, smoke or snow, so the
entities are named "Modeled UV ...". See home_config/uv-model-guide.md.

Run:  python3 uv_model.py            # daemon, publishes every UV_INTERVAL seconds
      python3 uv_model.py --once     # print one payload, no MQTT
      python3 uv_model.py --day 2026-12-21   # print the payload for another date
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import os
import sys
import time
from zoneinfo import ZoneInfo

LAT = float(os.environ.get("UV_LAT", "40.76"))
LON = float(os.environ.get("UV_LON", "-111.89"))
UV_A = float(os.environ.get("UV_A", "11.15"))
UV_P = float(os.environ.get("UV_P", "2.32"))
UV_THRESHOLD = float(os.environ.get("UV_THRESHOLD", "3.0"))  # WHO: protection at UVI >= 3
TZ = ZoneInfo(os.environ.get("TZ", "America/Denver"))

MQTT_HOST = os.environ.get("MQTT_HOST", "192.168.10.217")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
INTERVAL = int(os.environ.get("UV_INTERVAL", "300"))
DISCOVERY_PREFIX = os.environ.get("DISCOVERY_PREFIX", "homeassistant")
TOPIC_STATE = os.environ.get("UV_TOPIC", "uv_model/state")
TOPIC_AVAIL = TOPIC_STATE.rsplit("/", 1)[0] + "/status"

CURVE_START_HOUR = 5    # local hours covered by the daily curve attribute
CURVE_END_HOUR = 21
CURVE_STEP_MIN = 15

LOG = logging.getLogger("uv_model")


# ---------------------------------------------------------------- solar geometry (NOAA)
def _fractional_year(when_utc: dt.datetime) -> float:
    doy = when_utc.timetuple().tm_yday
    hour = when_utc.hour + when_utc.minute / 60 + when_utc.second / 3600
    return 2 * math.pi / 365 * (doy - 1 + (hour - 12) / 24)


def equation_of_time_min(when_utc: dt.datetime) -> float:
    g = _fractional_year(when_utc)
    return 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                     - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))


def declination_rad(when_utc: dt.datetime) -> float:
    g = _fractional_year(when_utc)
    return (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))


def solar_elevation_deg(when: dt.datetime) -> float:
    """Sun elevation above the horizon (degrees, no refraction) at an aware datetime."""
    u = when.astimezone(dt.timezone.utc)
    hour = u.hour + u.minute / 60 + u.second / 3600
    eqt = equation_of_time_min(u)
    decl = declination_rad(u)
    true_solar_min = (hour * 60 + eqt + 4 * LON) % 1440
    hour_angle = math.radians(true_solar_min / 4 - 180)
    phi = math.radians(LAT)
    sin_el = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(hour_angle)
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_el))))


def solar_noon(day: dt.date) -> dt.datetime:
    """Local solar noon for a calendar day (aware, in TZ)."""
    approx = dt.datetime(day.year, day.month, day.day, 12, tzinfo=TZ).astimezone(dt.timezone.utc)
    eqt = equation_of_time_min(approx)
    noon_utc_min = 720 - 4 * LON - eqt
    midnight_utc = dt.datetime(approx.year, approx.month, approx.day, tzinfo=dt.timezone.utc)
    return (midnight_utc + dt.timedelta(minutes=noon_utc_min)).astimezone(TZ)


# ---------------------------------------------------------------- the model
def uvi_for_elevation(elevation_deg: float) -> float:
    if elevation_deg <= 0:
        return 0.0
    return UV_A * math.sin(math.radians(elevation_deg)) ** UV_P


def uvi_at(when: dt.datetime) -> float:
    return uvi_for_elevation(solar_elevation_deg(when))


def threshold_elevation_deg() -> float:
    """Sun elevation at which the modeled UVI crosses UV_THRESHOLD (34.6 deg for SLC fit)."""
    return math.degrees(math.asin((UV_THRESHOLD / UV_A) ** (1 / UV_P)))


def protection_window(day: dt.date) -> tuple[dt.datetime | None, dt.datetime | None]:
    """Start/end of modeled UVI >= UV_THRESHOLD today, or (None, None) if it never gets there."""
    noon = solar_noon(day)
    decl = declination_rad(noon.astimezone(dt.timezone.utc))
    phi = math.radians(LAT)
    cos_h = ((math.sin(math.radians(threshold_elevation_deg())) - math.sin(phi) * math.sin(decl))
             / (math.cos(phi) * math.cos(decl)))
    if cos_h >= 1:
        return None, None
    half = dt.timedelta(hours=math.degrees(math.acos(max(-1.0, cos_h))) / 15)
    return noon - half, noon + half


def level_for(uvi: float) -> str:
    if uvi < 3:
        return "low"
    if uvi < 6:
        return "moderate"
    if uvi < 8:
        return "high"
    if uvi < 11:
        return "very_high"
    return "extreme"


def day_curve(day: dt.date) -> list[dict]:
    """Modeled UVI every CURVE_STEP_MIN minutes across the daylight hours (local clock)."""
    points = []
    t = dt.datetime(day.year, day.month, day.day, CURVE_START_HOUR, tzinfo=TZ)
    end = dt.datetime(day.year, day.month, day.day, CURVE_END_HOUR, tzinfo=TZ)
    while t <= end:
        points.append({"t": t.strftime("%H:%M"), "uvi": round(uvi_at(t), 2)})
        t += dt.timedelta(minutes=CURVE_STEP_MIN)
    return points


def _iso(when: dt.datetime | None) -> str | None:
    return when.replace(microsecond=0).isoformat() if when else None


def build_payload(now: dt.datetime | None = None) -> dict:
    now = (now or dt.datetime.now(TZ)).astimezone(TZ)
    day = now.date()
    noon = solar_noon(day)
    elevation = solar_elevation_deg(now)
    uvi = uvi_for_elevation(elevation)
    peak = uvi_at(noon)
    start, end = protection_window(day)
    return {
        "ts": _iso(now),
        "date": day.isoformat(),
        "uvi": round(uvi, 2),
        "uvi_peak": round(peak, 2),
        "level": level_for(uvi),
        "protection": uvi >= UV_THRESHOLD,
        "solar_elevation": round(elevation, 2),
        "solar_noon": _iso(noon),
        "uv3_start": _iso(start),
        "uv3_end": _iso(end),
        "threshold": UV_THRESHOLD,
        "threshold_elevation": round(threshold_elevation_deg(), 2),
        "curve": day_curve(day),
        "model": {"a": UV_A, "p": UV_P, "lat": LAT, "lon": LON, "kind": "clear-sky climatology fit"},
    }


# ---------------------------------------------------------------- MQTT / HA discovery
DEVICE = {
    "identifiers": ["uv_model"],
    "name": "Modeled UV",
    "manufacturer": "home_config/uv-model",
    "model": f"UVI = {UV_A} * sin(elevation)^{UV_P}",
}


def discovery_configs() -> list[tuple[str, dict]]:
    # HA prefixes the device name, so friendly names come out as "Modeled UV Index" etc.
    common = {
        "has_entity_name": True,
        "state_topic": TOPIC_STATE,
        "availability_topic": TOPIC_AVAIL,
        "device": DEVICE,
    }
    peak_attrs = ("{{ {'date': value_json.date, 'solar_noon': value_json.solar_noon, "
                  "'uv3_start': value_json.uv3_start, 'uv3_end': value_json.uv3_end, "
                  "'threshold': value_json.threshold, 'threshold_elevation': value_json.threshold_elevation, "
                  "'curve': value_json.curve, 'model': value_json.model} | to_json }}")
    return [
        (f"{DISCOVERY_PREFIX}/sensor/uv_model/modeled_uv_index/config", {
            **common,
            "name": "Index",
            "unique_id": "uv_model_index",
            "object_id": "modeled_uv_index",
            "value_template": "{{ value_json.uvi | round(1) }}",
            "unit_of_measurement": "UVI",
            "state_class": "measurement",
            "icon": "mdi:sun-wireless",
            "json_attributes_topic": TOPIC_STATE,
            "json_attributes_template": ("{{ {'level': value_json.level, 'solar_elevation': value_json.solar_elevation} "
                                         "| to_json }}"),
        }),
        (f"{DISCOVERY_PREFIX}/sensor/uv_model/modeled_uv_peak_today/config", {
            **common,
            "name": "Peak Today",
            "unique_id": "uv_model_peak_today",
            "object_id": "modeled_uv_peak_today",
            "value_template": "{{ value_json.uvi_peak | round(1) }}",
            "unit_of_measurement": "UVI",
            "state_class": "measurement",
            "icon": "mdi:weather-sunny",
            "json_attributes_topic": TOPIC_STATE,
            "json_attributes_template": peak_attrs,
        }),
        (f"{DISCOVERY_PREFIX}/binary_sensor/uv_model/uv_protection_recommended/config", {
            **common,
            "name": "Protection Recommended",
            "unique_id": "uv_model_protection",
            "object_id": "uv_protection_recommended",
            "value_template": "{{ 'ON' if value_json.protection else 'OFF' }}",
            "icon": "mdi:sunglasses",
        }),
        (f"{DISCOVERY_PREFIX}/sensor/uv_model/uv_protection_start/config", {
            **common,
            "name": "Protection Start",
            "unique_id": "uv_model_protection_start",
            "object_id": "uv_protection_start",
            "value_template": "{{ value_json.uv3_start or 'None' }}",
            "device_class": "timestamp",
            "icon": "mdi:weather-sunset-up",
        }),
        (f"{DISCOVERY_PREFIX}/sensor/uv_model/uv_protection_end/config", {
            **common,
            "name": "Protection End",
            "unique_id": "uv_model_protection_end",
            "object_id": "uv_protection_end",
            "value_template": "{{ value_json.uv3_end or 'None' }}",
            "device_class": "timestamp",
            "icon": "mdi:weather-sunset-down",
        }),
    ]


def run_daemon() -> None:
    import paho.mqtt.client as mqtt

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="uv-model")
    client.will_set(TOPIC_AVAIL, "offline", retain=True)

    def on_connect(c, _userdata, _flags, reason_code, _props):
        LOG.info("connected to %s:%s (%s)", MQTT_HOST, MQTT_PORT, reason_code)
        for topic, cfg in discovery_configs():
            c.publish(topic, json.dumps(cfg), retain=True)
        c.publish(TOPIC_AVAIL, "online", retain=True)
        c.publish(TOPIC_STATE, json.dumps(build_payload()), retain=True)

    client.on_connect = on_connect
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()
    try:
        while True:
            # Align ticks to the interval so the timestamps look tidy in HA history.
            time.sleep(INTERVAL - (time.time() % INTERVAL))
            payload = build_payload()
            client.publish(TOPIC_STATE, json.dumps(payload), retain=True)
            LOG.info("uvi=%.2f peak=%.2f elev=%.1f protection=%s window=%s..%s",
                     payload["uvi"], payload["uvi_peak"], payload["solar_elevation"],
                     payload["protection"], payload["uv3_start"], payload["uv3_end"])
    finally:
        client.publish(TOPIC_AVAIL, "offline", retain=True)
        client.loop_stop()
        client.disconnect()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true", help="print one payload and exit (no MQTT)")
    parser.add_argument("--day", help="YYYY-MM-DD: print the payload as of solar noon on that day")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.day:
        day = dt.date.fromisoformat(args.day)
        print(json.dumps(build_payload(solar_noon(day)), indent=2))
        return 0
    if args.once:
        print(json.dumps(build_payload(), indent=2))
        return 0
    run_daemon()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
