"""Pushover (house convention: creds from cecret_lake/pushover/.env via env_file) + MQTT."""
import json
import logging
import os
import urllib.parse
import urllib.request

LOG = logging.getLogger("notify")
MQTT_HOST = os.environ.get("MQTT_HOST", "192.168.10.217")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))


def pushover(title: str, message: str, url: str = "", url_title: str = "", priority: int = 0) -> bool:
    """Best-effort; a failed notification must never break a scan."""
    token, user = os.environ.get("PUSHOVER_API"), os.environ.get("PUSHOVER_USER")
    if not token or not user:
        LOG.warning("pushover creds missing (PUSHOVER_API/PUSHOVER_USER); not sent: %s", title)
        return False
    fields = {"token": token, "user": user, "title": title, "message": message, "priority": priority}
    if os.environ.get("PUSHOVER_DEVICE"):
        fields["device"] = os.environ["PUSHOVER_DEVICE"]
    else:
        LOG.warning("PUSHOVER_DEVICE unset; alert goes to all devices")
    if url:
        fields["url"] = url
        fields["url_title"] = url_title or "Open in Google Flights"
    try:
        req = urllib.request.Request("https://api.pushover.net/1/messages.json",
                                     data=urllib.parse.urlencode(fields).encode())
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        LOG.info("pushover sent: %s", title)
        return True
    except Exception as exc:  # noqa: BLE001
        LOG.warning("pushover failed: %r", exc)
        return False


def mqtt_publish(messages: list[tuple[str, dict, bool]]) -> None:
    """Publish [(topic, payload, retain)] on a short-lived connection."""
    try:
        import paho.mqtt.client as mqtt
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="flight-watch")
        c.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
        c.loop_start()
        for topic, payload, retain in messages:
            c.publish(topic, json.dumps(payload), retain=retain).wait_for_publish(5)
        c.loop_stop()
        c.disconnect()
    except Exception as exc:  # noqa: BLE001
        LOG.warning("mqtt publish failed: %r", exc)
