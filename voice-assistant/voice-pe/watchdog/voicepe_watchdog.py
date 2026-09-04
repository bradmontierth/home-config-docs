#!/usr/bin/env python3
"""Bridge-audio watchdog for the Voice PE / Echo Dot bridges (Beelink).

Runs once per minute from a user systemd timer. Each bridge exposes /health;
a mic is BAD when the bridge is unreachable, `audio_started` is false, or the
audio stream went stale. After ALERT_AFTER_S of continuous BAD it pushes one
Pushover alert carrying what it can see right now (TCP probe of the device,
HA heap/WiFi/uptime/reset-reason sensors), then one recovery push when the
audio is back. Why: Simon's Voice PE wedged for 20 min on 2026-09-04 with a
red ring and nobody knew until someone walked in; the alert points at
voice-assistant/tools/voicepe_diag.sh so the cause is captured while it is
happening, not reconstructed afterwards.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BRIDGES = {
    # sat: (health url, device ip for the TCP probe or None, HA entity stem)
    "simon": ("http://127.0.0.1:8793/health", "192.168.30.62", "simon_voice_pe"),
    "claire": ("http://127.0.0.1:8795/health", "192.168.30.60", "claire_voice_pe"),
    "master": ("http://127.0.0.1:8796/health", None, None),
}
ALERT_AFTER_S = int(os.environ.get("ALERT_AFTER_S", "180"))
STALE_AUDIO_S = float(os.environ.get("STALE_AUDIO_S", "60"))
STATE_FILE = Path(os.environ.get("STATE_FILE", "/home/pi/.local/state/voicepe-watchdog/state.json"))
PUSHOVER_ENV = Path(os.environ.get("PUSHOVER_ENV", "/home/pi/cecret_lake/pushover/.env"))
HA_TOKEN_FILE = Path(os.environ.get("HA_TOKEN_FILE", "/home/pi/cecret_lake/dashboard_webapp/ha_token"))
HA = os.environ.get("HA_BASE", "http://127.0.0.1:8123")
DIAG_HINT = "run: voice-assistant/tools/voicepe_diag.sh {sat}"


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def pushover(title: str, message: str, priority: int = 0) -> None:
    """Best-effort; PUSHOVER_DEVICE from cecret_lake is the one source of the
    device name (never hardcode a fallback — see pushover-device-convention)."""
    try:
        env = {}
        for line in PUSHOVER_ENV.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
        fields = {"token": env["PUSHOVER_API"], "user": env["PUSHOVER_USER"],
                  "title": title, "message": message[:1000], "priority": priority}
        if env.get("PUSHOVER_DEVICE"):
            fields["device"] = env["PUSHOVER_DEVICE"]
        req = urllib.request.Request("https://api.pushover.net/1/messages.json",
                                     data=urllib.parse.urlencode(fields).encode())
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
    except Exception as exc:  # noqa: BLE001
        log(f"pushover failed: {exc}")


def health(url: str) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            d = json.load(r)
    except Exception as exc:  # noqa: BLE001
        return False, f"bridge unreachable ({exc.__class__.__name__})"
    if not d.get("audio_started"):
        return False, f"audio_started=false mode={d.get('mode')} armed={d.get('active_armed')}"
    age = d.get("last_audio_age_s")
    if age is not None and age > STALE_AUDIO_S:
        return False, f"audio stale {age:.0f}s"
    return True, "ok"


def tcp_probe(ip: str | None) -> str:
    if not ip:
        return ""
    try:
        with socket.create_connection((ip, 6053), timeout=3):
            return f"tcp {ip}:6053 open"
    except Exception as exc:  # noqa: BLE001
        return f"tcp {ip}:6053 {exc.__class__.__name__} (no SYN-ACK = device IP stack, if ARP still answers)"


def ha_sensors(stem: str | None) -> str:
    if not stem or not HA_TOKEN_FILE.is_file():
        return ""
    try:
        req = urllib.request.Request(f"{HA}/api/states", headers={
            "Authorization": f"Bearer {HA_TOKEN_FILE.read_text().strip()}"})
        with urllib.request.urlopen(req, timeout=8) as r:
            states = json.load(r)
    except Exception as exc:  # noqa: BLE001
        return f"HA: {exc.__class__.__name__}"
    want = ("free_heap", "largest_free_block", "loop_time", "wifi_signal", "uptime", "reset_reason", "bssid")
    out = []
    for s in states:
        e = s["entity_id"]
        if stem in e and any(w in e for w in want):
            short = e.split(f"{stem}_", 1)[-1]
            out.append(f"{short}={s['state']}{s['attributes'].get('unit_of_measurement', '')}@{s['last_updated'][11:19]}Z")
    return "HA: " + (", ".join(sorted(out)) if out else "no sensor entities")


def log_sensors(sat: str) -> str:
    """Latest debug-sensor values from the <sat>-voice-pe-logs container —
    independent of HA (HA 2025.11 cannot register most of these entities)."""
    if sat not in ("simon", "claire"):
        return ""
    try:
        out = subprocess.run(
            ["docker", "logs", "--since", "10m", f"{sat}-voice-pe-logs"],
            capture_output=True, text=True, timeout=10).stdout
    except Exception as exc:  # noqa: BLE001
        return f"log: {exc.__class__.__name__}"
    latest: dict[str, str] = {}
    # ESPHome 2026: "[S][sensor]: 'Free heap' >> 117416 B"; older builds:
    # "[D][sensor:093]: 'Free heap': Sending state 117416.00000 B with 0 decimals".
    for m in re.finditer(r"\]\[(?:sensor|text_sensor)(?::\d+)?\]: '([^']+)'(?: >> |: Sending state )([^\n]+)", out):
        latest[m.group(1)] = m.group(2).split(" with ")[0].strip()[:24]
    if not latest:
        return "log: no sensor lines in 10 min (device not logging = not connected)"
    return "log: " + ", ".join(f"{k}={v}" for k, v in sorted(latest.items()))


def main() -> int:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        state = json.loads(STATE_FILE.read_text())
    except Exception:  # noqa: BLE001
        state = {}
    now = time.time()
    for sat, (url, ip, stem) in BRIDGES.items():
        ok, why = health(url)
        st = state.setdefault(sat, {"bad_since": None, "alerted": False})
        if ok:
            if st["alerted"]:
                down = now - (st["bad_since"] or now)
                log(f"{sat}: recovered after {down/60:.1f} min")
                pushover(f"{sat} mic back", f"audio streaming again after {down/60:.0f} min down. "
                         f"{ha_sensors(stem)} {log_sensors(sat)}")
            st["bad_since"] = None
            st["alerted"] = False
            continue
        st["bad_since"] = st["bad_since"] or now
        bad_for = now - st["bad_since"]
        log(f"{sat}: BAD {bad_for:.0f}s — {why}")
        if bad_for >= ALERT_AFTER_S and not st["alerted"]:
            detail = " | ".join(x for x in (why, tcp_probe(ip), ha_sensors(stem), log_sensors(sat)) if x)
            pushover(f"{sat} mic down {bad_for/60:.0f} min", f"{detail}\n{DIAG_HINT.format(sat=sat)}", priority=0)
            st["alerted"] = True
    STATE_FILE.write_text(json.dumps(state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
