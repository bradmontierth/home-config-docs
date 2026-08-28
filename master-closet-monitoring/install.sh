#!/bin/bash
# Run ON the closet Pi from the directory holding these files. Idempotent.
set -eu
sudo install -m755 master-closet-metrics-mqtt.sh /usr/local/bin/master-closet-metrics-mqtt
sudo install -m755 master-closet-wifi-watchdog.sh /usr/local/sbin/master-closet-wifi-watchdog
sudo install -m644 master-closet-metrics-mqtt.service master-closet-wifi-watchdog.service master-closet-wifi-watchdog.timer /etc/systemd/system/
sudo mkdir -p /etc/systemd/journald.conf.d /etc/systemd/system.conf.d
sudo install -m644 journald-persistent.conf /etc/systemd/journald.conf.d/persistent.conf
sudo install -m644 system-watchdog.conf /etc/systemd/system.conf.d/watchdog.conf
dpkg -s mosquitto-clients >/dev/null 2>&1 || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q mosquitto-clients
sudo systemctl daemon-reload
sudo systemctl daemon-reexec
sudo systemctl restart systemd-journald
sudo systemctl enable --now master-closet-metrics-mqtt.service master-closet-wifi-watchdog.timer
