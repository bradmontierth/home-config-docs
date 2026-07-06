# Plex Host Guide

The Plex Raspberry Pi is available on the local network as:

```bash
ssh plex
```

Expected SSH config entry:

```sshconfig
Host plex
  HostName 192.168.10.150
  User pi
  IdentityFile ~/.ssh/id_ed25519_plex
  IdentitiesOnly yes
```

## Device role

`plex` is the Raspberry Pi that runs the home Plex media server.

Plex runs in Docker on this host.

This host also runs Transmission for downloading new content, plus a local automation script that processes completed downloads and moves them into the Plex media library.

## Main services

High-level layout:

```text
plex
├── Docker
│   ├── Plex Media Server
│   └── Transmission
├── Download processing script
├── External USB hard drive
└── WireGuard-routed download network
```

## Plex

Plex Media Server runs in Docker.

It ingests media from the Plex library directory on the connected storage.



## Transmission

Transmission is used to download new content.

Transmission runs in a network path that only has access through the configured WireGuard connection. Downloads should be routed through WireGuard rather than directly through the normal network path.


## Download processing

This host has a script that automatically:

1. Cleans completed downloads
2. Parses downloaded content
3. Scrubs unwanted files or naming issues
4. Moves completed media into the Plex directory
5. Allows Plex to ingest the media

When troubleshooting missing or incorrectly imported media, check:

- Whether Transmission completed the download
- Whether the processing script ran
- Whether the script moved files to the expected Plex library path
- Whether Plex has scanned the library
- Whether file ownership and permissions allow Plex to read the files
