# Home Audio Pi Guide

The whole-home audio Raspberry Pi is available on the local network as:

```bash
ssh home-audio-pi
```

Expected SSH config entry:

```sshconfig
Host home-audio-pi
  HostName 192.168.10.140
  User pi
  IdentityFile ~/.ssh/id_ed25519_home_audio_pi
  IdentitiesOnly yes
```

## Device role

`home-audio-pi` is the central audio host for the whole-home audio system.

It provides audio for **five zones** connected to an amplifier.

## Audio hardware layout

Zones 1 through 4 use an 8-channel / 4-zone Raspberry Pi DAC HAT.

The fifth zone, the **loft**, uses a Behringer USB audio DAC.

High-level layout:

```text
home-audio-pi
├── Raspberry Pi DAC HAT
│   ├── Zone 1
│   ├── Zone 2
│   ├── Zone 3
│   └── Zone 4
└── Behringer USB DAC
    └── Zone 5 / Loft
```

All zones ultimately feed into the connected amplifier.

**numbered zones 1-4/5 may not line up with zone numbers on the amp or other internal numbers. Simply 1-4 for enumeration sake here.

## Snapcast setup

Snapserver runs on `home-audio-pi`.

Each audio zone is represented by a Snapcast client.

Music Assistant discovers and controls these devices through Snapcast.

## Home Assistant and Music Assistant

Home Assistant is not running on Pi.

Home Assistant and Music Assistant are running on the Beelink mini PC.

Music Assistant brings in the whole-home audio zones from `home-audio-pi` through Snapcast.

High-level control flow:

```text
Home Assistant / Music Assistant on Beelink
        ↓
Snapcast integration
        ↓
Snapserver on home-audio-pi
        ↓
Snapcast clients / audio outputs
        ↓
DAC HAT + Behringer USB DAC
        ↓
Amplifier
        ↓
Five audio zones
```


## Adapter Snapcast self-heal

The local `home-audio-adapter` service is configured with the Snapcast control
API for `home-audio-pi`:

```text
SNAPCAST_HOST=192.168.10.140
SNAPCAST_CONTROL_PORT=1705
```

When AntennaPod or Tempo asks the adapter to play through Music Assistant,
Music Assistant may occasionally fail because Snapserver still has a stale idle
dynamic stream such as `Music Assistant - shower`. In that case Music Assistant
reports errors like:

```text
Unable to create stream - No free port found?
Stream with name 'Music Assistant - shower' already exists
```

The adapter treats that as recoverable. It queries Snapserver, removes only the
matching idle `Music Assistant - <player>` stream with no connected clients, and
retries the original play request once. It also retries when Music Assistant
accepts the play request but remains idle because the target stream was stale.

Manual fallback, if automatic repair does not clear it:

```bash
python3 - <<'PY'
import json, socket

stream_id = "Music Assistant - shower"
host = "192.168.10.140"
port = 1705

def rpc(method, params=None):
    payload = {"id": 1, "jsonrpc": "2.0", "method": method}
    if params is not None:
        payload["params"] = params
    with socket.create_connection((host, port), timeout=3) as sock:
        sock.sendall((json.dumps(payload) + "\n").encode())
        sock.settimeout(3)
        return json.loads(sock.recv(1024 * 1024).decode())

status = rpc("Server.GetStatus")["result"]["server"]
stream = next((item for item in status["streams"] if item["id"] == stream_id), None)
attached = [
    client
    for group in status["groups"]
    if group.get("stream_id") == stream_id
    for client in group.get("clients", [])
    if client.get("connected")
]
if stream and stream.get("status") == "idle" and not attached:
    print(rpc("Stream.RemoveStream", {"id": stream_id}))
else:
    print("Not removing active or missing stream", stream)
PY
```
