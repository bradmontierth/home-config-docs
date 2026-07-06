# Transmission VPN Docker Architecture

This guide describes the Docker and network shape on the Plex/download Raspberry Pi.

Connect to the host with:

```bash
ssh plex
```

Primary compose file:

```text
/home/pi/transmission/docker-compose.yml
```

## High-Level Shape

Transmission is intentionally not attached directly to the normal Docker bridge or host network. It shares the network namespace of a Gluetun VPN container.

```text
LAN clients
    |
    | 192.168.10.150:9091
    v
Gluetun VPN container
    |
    +-- exposes selected LAN/local ports
    +-- owns VPN tunnel and firewall
    |
    v
Transmission container
    network_mode: service:vpn
```

Plex is separate from that VPN path:

```text
Plex container
    network_mode: host
    media mounted from /media/externalHDD/media
```

## Containers

Main active containers in the compose stack:

```text
vpn           qmcgaw/gluetun
transmission  linuxserver/transmission
plex          linuxserver/plex
aria2         wahyd4/aria2-ui
```

Sonarr, Radarr, and the old antivirus service are present in the compose file but commented out.

## VPN Container

The VPN service is:

```yaml
vpn:
  image: qmcgaw/gluetun
  container_name: vpn
  cap_add:
    - NET_ADMIN
```

It uses WireGuard configuration from:

```text
/home/pi/transmission/wireguard
/home/pi/transmission/gluetun-data
```

It exposes selected ports:

```text
192.168.10.150:9091 -> Transmission Web UI
127.0.0.1:8989     -> Sonarr, if enabled
127.0.0.1:7878     -> Radarr, if enabled
127.0.0.1:6880     -> aria2 web/RPC
127.0.0.1:6888     -> aria2 file browser
```

The important detail is that Transmission does not publish its own ports. Gluetun publishes the allowed ports for services sharing its network namespace.

## Transmission

Transmission service:

```yaml
transmission:
  image: linuxserver/transmission
  container_name: transmission
  network_mode: "service:vpn"
```

Because of `network_mode: service:vpn`, Transmission:

- uses the VPN container network namespace
- reaches the internet through Gluetun
- has no independent Docker network identity
- is reachable on the LAN only through ports published by Gluetun

Transmission host paths:

```text
/home/pi/transmission/transmission-config -> /config
/media/externalHDD/downloads              -> /downloads
/media/externalHDD/media                  -> /media
```

Important Transmission paths inside the container:

```text
/downloads/complete
/downloads/incomplete
/downloads/.pipeline-triggers
/config/hooks/transmission-done.sh
```

Matching host paths:

```text
/media/externalHDD/downloads/complete
/media/externalHDD/downloads/incomplete
/media/externalHDD/downloads/.pipeline-triggers
/home/pi/transmission/transmission-config/hooks/transmission-done.sh
```

## Completion Hook

Transmission runs this container-side hook when a torrent completes:

```text
/config/hooks/transmission-done.sh
```

The hook writes a trigger file to:

```text
/downloads/.pipeline-triggers
```

On the host that is:

```text
/media/externalHDD/downloads/.pipeline-triggers
```

The host systemd path unit watches for these files:

```text
/etc/systemd/system/media-pipeline-trigger.path
```

It starts:

```text
/etc/systemd/system/media-pipeline-trigger.service
```

Which runs:

```text
/home/pi/transmission/organizer/run_pipeline_locked.sh
```

The runner waits briefly, takes a lock, and invokes:

```text
/home/pi/transmission/organizer/plex_organize.py
```

This split exists because Transmission is inside a container, while the organizer needs the host Python environment, host tools, host ClamAV daemon, and host paths.

There is also a host-side fallback watcher:

```text
/etc/systemd/system/media-pipeline-complete.path
/etc/systemd/system/media-pipeline-complete.service
```

It watches:

```text
/media/externalHDD/downloads/complete/*
```

and runs:

```text
/home/pi/transmission/organizer/create_complete_trigger.sh
```

That script creates a trigger file if completed items exist and no trigger is already pending. This covers existing downloads and cases where Transmission's hook does not fire.

## Plex

Plex uses host networking:

```yaml
plex:
  image: linuxserver/plex
  container_name: plex
  network_mode: host
```

Plex media mount:

```text
/media/externalHDD/media -> /media
```

The pipeline writes approved media to host paths under:

```text
/media/externalHDD/media
```

Plex sees those files inside the container under:

```text
/media
```

## Safe Media Pipeline Boundary

The safe media pipeline is host-side by design:

```text
Transmission container
    |
    | writes trigger
    v
host systemd path unit
    |
    v
host organizer script
    |
    +-- host ClamAV daemon
    +-- host ffmpeg/ffprobe
    +-- host MariaDB logging over LAN
    +-- host external disk paths
```

Do not move the organizer into the Transmission container without rethinking:

- ClamAV daemon access
- Python dependencies
- MariaDB network access
- host path mapping
- file ownership
- lock behavior

## Operational Checks

Check container status:

```bash
ssh plex
cd /home/pi/transmission
docker compose ps
```

Check VPN and Transmission processes:

```bash
ssh plex
docker logs --tail 100 vpn
docker logs --tail 100 transmission
```

Check the hook and pipeline trigger:

```bash
ssh plex
systemctl status media-pipeline-trigger.path
systemctl status media-pipeline-trigger.service
systemctl status media-pipeline-complete.path
systemctl status media-pipeline-complete.service
tail -n 100 /home/pi/transmission/transmission-config/media-pipeline-hook.log
tail -n 100 /home/pi/transmission/organizer/transmission-hook-runner.log
```

Check published ports from the host:

```bash
ssh plex
docker port vpn
```

Transmission Web UI should be reachable on the LAN at:

```text
http://192.168.10.150:9091
```

## Failure Modes

If Transmission is unreachable but the container is running, check Gluetun first. Transmission depends on the VPN container network namespace.

If the VPN container is unhealthy, Transmission may be running but effectively isolated.

If completed downloads do not process, check:

1. Transmission hook settings in `/home/pi/transmission/transmission-config/settings.json`
2. hook log under `/home/pi/transmission/transmission-config/media-pipeline-hook.log`
3. trigger files under `/media/externalHDD/downloads/.pipeline-triggers`
4. `media-pipeline-trigger.path`
5. organizer runner log
6. MariaDB/Grafana pipeline run records

If the external disk is nearly full, all of the above can appear slow because downloads, ClamAV, `ffprobe`, CUE splitting, and file moves all contend for `/media/externalHDD`.
