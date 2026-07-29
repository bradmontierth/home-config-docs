# Pushover alerting convention

Audited 2026-07-29 while shipping the MA announcement watchdog.

## Source of truth

`/home/pi/cecret_lake/pushover/.env` — three keys:

```
PUSHOVER_API=      # app token
PUSHOVER_USER=     # user/group key
PUSHOVER_DEVICE=   # canonical target device name
```

**A phone upgrade should be one edit to `PUSHOVER_DEVICE` in that file, and
nothing else.** That is the whole point of the convention. Two rules follow:

1. **Read the device name; never hardcode it.** Not as a constant, and not as a
   fallback default either.
2. **No fallback device name.** If `PUSHOVER_DEVICE` is unset, omit the field and
   let it broadcast to all devices — a noisy alert you *see* beats a targeted
   alert silently delivered to a phone that no longer exists. Log a warning.

Reference implementation: `ma-announce-watchdog/ma_announce_watchdog.py::pushover()`.

Containers get it by mounting the file as `env_file` (see
`jobs/docker-compose.yml`, `site-notifier/docker-compose.yml`,
`my_photo_app/docker-compose.yml`). Host scripts read the path directly. Never
copy the values into code, compose files, or logs — reference the path.

## Current state (audit)

| Consumer | Creds from | Sends `device`? | Phone-upgrade safe? |
|---|---|---|---|
| `ma-announce-watchdog` | shared `.env` | yes | ✅ |
| `site-notifier` | shared `.env` | yes, if set | ✅ |
| `jobs` | shared `.env` | yes, from its **own** settings store | ⚠️ second place to update |
| `my_photo_app` | shared `.env` | no → all devices | ⚠️ untargeted |
| `podcast/bin/notify_pushover.sh` | own env | yes, **hardcoded fallback `pixel8`** | ❌ landmine |
| `doorbell_tts/shim` | own compose env | yes, if set | ⚠️ separate env |
| `scripts/homelab_backup` | own config dict | no | ⚠️ separate config |
| **Node-RED (105 `pushover api` nodes)** | own `pushover-keys` config node | no — `device: null` on all 105 | ⚠️ untargeted |

So today **most alerts — including the nightly bedtime summary — go to every
device on the account**, and exactly one place (`podcast`) will silently
misroute on a phone upgrade.

## Recommended cleanup (not yet done)

Roughly in value order:

1. **`podcast/bin/notify_pushover.sh`** — drop the `:-pixel8` fallback. One line;
   the only true breakage on an upgrade.
2. **`jobs`** — have `get_alert_device()` default to the shared `PUSHOVER_DEVICE`
   so there is one place to edit, not two.
3. **Node-RED** — the 105 nodes share one `pushover-keys` credential node, but
   device is per-node and null everywhere. Targeting them all means either a
   scripted flow edit or accepting broadcast. Worth deciding deliberately:
   household-wide alerts (water running, garage left open) arguably *should*
   broadcast; personal ones (backup failed, watchdog fired) should not.
4. **`my_photo_app`, `homelab_backup`, `doorbell_tts`** — point at the shared
   `.env` and pass `device`.

Until then, assume any alert not in the ✅ rows lands on every device.
