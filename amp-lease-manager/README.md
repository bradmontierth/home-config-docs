# Whole-Home Amp Lease Manager

This service is the sole lifecycle-policy owner for the whole-home amplifier's
ZEN16 R3 trigger. Home Assistant remains the hardware control path. Node-RED
and `home-audio-adapter` acquire readiness through this API.

Safety properties:

- The relay entity is fixed in server configuration.
- Startup and periodic validation require the entity to map to the expected
  immutable Z-Wave R3 unique ID.
- No caller may supply a Home Assistant entity ID.
- Automatic off is disabled by default and inhibited whenever Home Assistant
  or Music Assistant state is uncertain.

The production cold-readiness interval is five seconds. Four seconds lost the
opening number in one of three recorded cold trials. A 4.25-second follow-up
damaged `zero` to `So` on its first cold trial, and a 4.5-second follow-up lost
`zero` entirely on its first cold trial. Five seconds passed three of three
with complete `Zero, one, two...` microphone transcripts.

## API

- `POST /v1/touch`: one-shot activity plus ten-minute hold.
- `POST /v1/acquire`: acquire/renew a named playback lease.
- `POST /v1/renew`: renew a playing session without issuing a relay-on command.
- `POST /v1/release`: release a named playback lease into the hold window.
- `GET /v1/status`: explain current policy, relay, dependency, and lease state.
- `GET /healthz` and `GET /readyz`: liveness and reconciled readiness.

Mutating and status requests use `Authorization: Bearer <token>`.

## Production lifecycle

The service code defaults automatic off to disabled for a fresh deployment.
This installation has completed consumer and fixed-R3 fallback validation and
deploys `ALLOW_AUTOMATIC_OFF=true` with a 600-second hold. Any active explicit
lease, observed Music Assistant playback, HA/MA uncertainty, or failed R3
identity validation inhibits shutdown.

## Operations

```bash
cd /home/pi/amp-lease-manager
docker compose ps
docker logs --tail 100 amp-lease-manager
docker compose up -d --build
```

Configuration lives in the permission-restricted secret-lake environment file.
The Home Assistant and manager API tokens are mounted read-only from separate
files. Do not place either token in source control or log output.

The source, Compose definition, and tests are included in the homelab backup
configuration. Runtime state under `data/` is deliberately excluded; startup
reconciliation safely reconstructs policy from unexpired persisted state when
the live volume exists, or from actual HA/MA state after a restore.
