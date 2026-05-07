# Kitchen Speaker Host Guide

This directory assumes agents may need to understand and troubleshoot the `kitchen-speaker` host.

## SSH access

The kitchen speaker is reachable over SSH using the local SSH config alias:

```bash
ssh kitchen-speaker
```

The SSH config entry is expected to look like this:

```sshconfig
Host kitchen-speaker
  HostName 192.168.10.24
  User pi
  IdentityFile ~/.ssh/id_ed25519_kitchen_speaker
  IdentitiesOnly yes
```

Agents should use `ssh kitchen-speaker` rather than hard-coding the IP address or key path in commands, unless specifically debugging SSH configuration.

## Device role

The kitchen speaker is a Raspberry Pi / Linux-based speaker endpoint connected to a physical speaker.

It serves two media roles at the same time:

1. **GMediaRenderer / DLNA renderer**
   - Runs a GMediaRenderer endpoint.
   - Is added to Home Assistant as a media player.
   - Home Assistant uses this media player for text-to-speech announcements.
   - When troubleshooting TTS playback, check the DLNA / GMediaRenderer service and Home Assistant media player integration.

2. **Spotify Connect / LibreSpot device**
   - Runs LibreSpot as a Spotify Connect endpoint.
   - Appears as a selectable playback device in the Spotify app.
   - Can be chosen directly from Spotify to play music through the same speaker.

Both the GMediaRenderer/DLNA endpoint and the LibreSpot Spotify Connect endpoint are connected to the same speaker output.

## Mental model

Treat this host as a shared audio endpoint:

- Home Assistant TTS announcements use the GMediaRenderer/DLNA path.
- Spotify playback uses the LibreSpot/Spotify Connect path.
- Both paths ultimately output to the same physical speaker.
- Problems may be isolated to one path or may affect the shared audio stack.
