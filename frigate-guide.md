# Frigate Box SSH Guide

The Frigate machine is available on the local network as:

```bash
ssh frigate-box
```

Expected SSH config entry:

```sshconfig
Host frigate-box
  HostName 192.168.10.250
  User pi
  IdentityFile ~/.ssh/id_ed25519_frigate_box
  IdentitiesOnly yes
```

Use the alias instead of the raw IP when possible:

```bash
ssh frigate-box
```

This connects as user `pi` to `192.168.10.250` using the dedicated SSH key:

```text
~/.ssh/id_ed25519_frigate_box
```

## Quick Summary
Frigate is running in docker
Brought into HA via HACS
HA is on host machine, Frigate on the 192.168.10.250
Both HA and Frigate in docker.
