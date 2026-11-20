"""UDP debug-log sink for the Shelly TV plug (192.168.10.97, Sys debug.udp -> :5514).

Goal: capture what the plug logs around its unexplained self-restarts (2026-11-15,
2026-11-17), after which its power metering comes back dead. See
memory tv-power-shelly-detection / Node-RED "Misc Automations" watchdog.
"""
import logging, logging.handlers, socket
h = logging.handlers.RotatingFileHandler('/logs/shelly-tv-plug.log', maxBytes=10_000_000, backupCount=10)
h.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
log = logging.getLogger('s'); log.addHandler(h); log.setLevel(logging.INFO)
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(('0.0.0.0', 5514))
while True:
    data, addr = s.recvfrom(65535)
    for line in data.decode('utf-8', 'replace').splitlines():
        if line.strip():
            log.info('%s %s', addr[0], line.rstrip())
