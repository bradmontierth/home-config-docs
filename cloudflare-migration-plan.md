# Cloudflare migration plan: own domain, Trainer Max off the Funnel, car audio

2026-11-21. Status: Phase 0 done 2026-11-23 (domain + token, §0a); nothing deployed yet. Written after reading the live Funnel
config, `companion/deploy/strava_gate.py`, `companion/portal-gate/gate.py`, `companion/strava-hook/hook.py`,
`companion/strava-proxy/hooks.py`, `desktop/main.js` and `realtime/CAR-AUDIO-PLAN.md`.

## 0. DECIDED 2026-11-21 (Brad): the split. This overrides the sections below where they differ

- **Portal stays on Tailscale Funnel.** Reason: Funnel is end-to-end encrypted (TLS ends on the Beelink);
  Cloudflare would read Trainer Max traffic in the clear, and the gate is already well protected. So §4.3
  (portal move, Access, tunnel, `dl.` bucket, desktop-shell release) and §4.4 (Funnel off) are NOT happening.
  No address change for riders; 90-day sessions stay. `/strava/callback` stays a temporary Funnel path.
- **Car audio → Cloudflare** on `lights.<domain>` (§4.1). Nothing private in it.
- **Strava webhook → Cloudflare** on `hooks.<domain>` (§4.2). Events carry only activity and athlete IDs;
  home still fetches the activity from Strava itself. Removes one always-on listener at home.
- **End state:** WAN = WireGuard + Funnel with ONE always-on path (the portal gate) + outbound-only
  connections to Cloudflare. Phase 0 (domain, account lockdown, card for R2) is still step one.
- Still open: when to do the webhook (any time; car audio first, it's in season).

## 0a. Phase 0 DONE 2026-11-23

- **Domain: `rooflinehouse.com`**, bought at Cloudflare Registrar in Brad's existing Cloudflare account (the
  same account/login as illuminatehealthanalytics.com; one account is fine). Show at the apex or `lights.`,
  webhooks at `hooks.`. Tesla public key stays on illuminatehealthanalytics.com until the Fleet partner app is
  re-registered.
- **API token:** `/home/pi/cecret_lake/cloudflare/api_token` (dir 700, file 600). "Edit Cloudflare Workers"
  template + DNS Edit + Queues Edit, IP-filtered to the home WAN address (135.129.158.82 on 2026-11-23;
  if the ISP changes it the token fails auth until that field is edited), no expiry.
- **The token can reach BOTH zones, and Brad chose that on purpose** (2026-11-23: Claude already manages the
  illuminate side, so per-zone separation adds little; the IP filter covers a leaked token). Don't "fix" it.
  Still: never change illuminatehealthanalytics.com records from this project unless asked.

## 1. Review: what we have, and what the earlier proposal got wrong

**Today's exposure (verified 2026-11-21):** one Funnel hostname, `beelink.tail65546d.ts.net`, three paths:

| Path | Open | Goes to | Holds |
|---|---|---|---|
| `/strava/wh-<secret>` | always | `trainermax-strava-hook` :8096 | nothing (no tokens, no DB); writes small JSON files to a queue dir |
| `/tm-<secret>/…` | always | `trainermax-portal-gate` :8101 | sessions only; stdlib Python; forwards allow-listed paths to the companion |
| `/strava/callback` | only while a Strava invite is open | companion :8093 directly | the companion itself (tokens, DB) |

Funnel is controlled by the system user `tmgate` (sole Tailscale operator), so nothing running as `pi` can
widen it. 52 days of webhook logs show only Strava. This is a careful setup; the migration is **not urgent
for security**. The reasons to do it are: a stable name we own, car audio needing a public host anyway, a
pattern for future apps, edge logs of what gets blocked, and turning pre-login traffic away before it
reaches the house.

**Corrections to what I told Brad earlier in chat:**

1. **Cloudflare Access cannot replace the gate.** The gate does per-athlete authorisation that Access has no
   concept of: your own pages only, read-only teammate views, cycling/running switches (`allowed()`),
   40 coach chats a day, one websocket (your own group-ride room), body-size caps. Access only answers
   "is this one of our five people". The gate stays. Access becomes an outer door in front of it.
2. **Access sessions last at most one month** (Cloudflare docs: "immediate timeout to 1 month"). The gate's
   are 90 days, renewed on use, so riders effectively never sign in again. With Access, each rider types an
   emailed code about once a month, per device.
3. **Cloudflare decrypts the traffic; Funnel does not.** With Funnel, TLS ends on the Beelink and Tailscale
   relays ciphertext. With Cloudflare, TLS ends at Cloudflare, which then sees every request and response
   in the clear: training data, coach chat, Rachel's data. That is the normal trade for any CDN, and
   Cloudflare is a reputable processor, but it is a real change from today and Brad should choose it
   knowingly (see §7 Q1).
4. **The Cloudflare account becomes a key to the house.** A dashboard-managed tunnel can be repointed by
   whoever holds the account at any service the tunnel container can reach. The design below removes
   that: tunnel config lives in a local file, and the tunnel container can reach only the gate.
5. **Changing the web address has real costs for Trainer Max** (§4.3): browser-stored settings, the offline
   cache, rides waiting to upload, the desktop app's built-in address and its update feed, links in
   notifications, and Strava's registered callback domain.

**Verdict:** worth doing, in this order: car audio first (new build, nothing to migrate, and the season is
now), then the Strava webhook (small, removes one always-on listener at home), then the portal after the
Christmas season, when a hiccup doesn't cost anyone a ride. Funnel stays up until the last step, so every
phase can be rolled back.

## 2. Target architecture

```
                    Cloudflare (own domain, e.g. <domain>.com)
  phones/cars ──▶ lights.<domain>   Pages (player) + Worker + Durable Object (show hub) + R2 (audio)
  Strava ───────▶ hooks.<domain>    Worker: checks + stores webhook events (Queue, pull)
  riders ───────▶ coach.<domain>    Access (email code, 5 named people) ─▶ Tunnel
  shell updates ▶ dl.<domain>       R2 public bucket: desktop installer + update feed
                         ▲ outbound only                      ▲ outbound only
  ───────────────────────┼──────────────────────────────────┼──────────────── home
   carcast sidecar (Studio status → hub)        cloudflared (own container, own Docker network)
   hook puller (Queue → hookq dir)                    └─▶ trainermax-portal-gate ─▶ companion
```

What answers the internet from home afterwards: nothing unauthenticated. The only inbound requests are
riders who have passed Access, arriving through an outbound tunnel, landing on the same gate as today.

## 3. Phase 0: domain and account (about an hour, Brad does the clicking)

1. Buy a personal `.com` at Cloudflare Registrar (at cost, about $10.46/yr, renews at the same price).
   Not the LLC's domain. A neutral name, since `lights.` and `coach.` both hang off it.
2. Cloudflare account security, before anything points at home:
   - hardware-key or passkey 2FA, no SMS; recovery codes printed, not in a password manager on the Beelink;
   - API tokens: one per job, least scope (`Workers Scripts: Edit` for deploys; `Queues: pull` for the hook
     puller). No Global API Key anywhere. Tokens live by path in `cecret_lake/cloudflare/`.
3. Zero Trust: create the team (free plan, 50 users), login method = one-time PIN to email.
4. Add a payment card (needed for R2; expected bill $0. Free: 10 GB, no egress fee).

## 4. The three moves

### 4.1 Car audio → `lights.<domain>` (first; see `realtime/CAR-AUDIO-PLAN.md`)

Build it Cloudflare-native as that plan's sections 6–7 describe: Pages for the player, a Worker + one
SQLite Durable Object as the hub (free plan since 2025-04-07), R2 for the AAC files, and the `carcast`
sidecar at home holding one outbound websocket. Skip the Funnel prototype phase: with the domain in hand
there is no reason to expose a new listener at home even briefly. Testing from the driveway works the same.
The one write back into the house stays "next song" via the sidecar.

### 4.2 Strava webhook → `hooks.<domain>` (second; about half a day)

- **Worker** at `hooks.<domain>/strava/wh-<same secret path>`: answers Strava's subscription check
  (verify token as a Worker secret), applies the same shape checks as `hook.py valid()`, 4 KB body cap,
  and puts the event on a Cloudflare Queue (free since 2026-02; 10,000 operations/day; **24 h retention**).
- **Puller at home** replaces the listener: a small loop (in the existing hook container, minus its HTTP
  server) pulls and acks over HTTPS and writes the same JSON files into `hookq/`. The companion's
  queue reader does not change. The owner check (`owners.json`) moves into the puller.
- **Cutover:** set `OAUTH_CALLBACK_BASE`/hook base to the new host. `hooks.ensure_subscription()` refuses to
  replace a subscription it didn't create at a different URL ("left alone"), and Strava allows one per app,
  so: delete the old subscription by hand (`DELETE /push_subscriptions/<id>`), let the loop create the new
  one, confirm "subscribed now". Events during the gap of a minute or two are only hints; check that the
  importer's periodic catch-up covers them (and the >24 h home-outage case) before relying on it.
- **Rollback:** point the base back at the ts.net host and re-create the subscription.

### 4.3 Portal → `coach.<domain>` (third; after the season; 2–3 days with testing)

**At Cloudflare**
- Access application on `coach.<domain>`: Allow = the five named emails (from the gate's `users.json`),
  one-time PIN, session 1 month.
- `dl.<domain>`: public R2 bucket for `latest.yml`, the installer (121 MB) and blockmaps. The installer is
  not secret, the updater sends no cookie, and Cloudflare's terms want large files on R2 rather than
  proxied. `publish-desktop.sh` uploads there.

**At home**
- `cloudflared` in its own container: non-root, read-only, on a Docker network whose only other member is
  `trainermax-portal-gate`. **Locally-managed config** (`config.yml` + credentials file in
  `cecret_lake/cloudflare/`), so the dashboard cannot add routes. One ingress rule,
  `coach.<domain>` → `http://portal-gate:8101`, with `originRequest.access: {required: true, teamName,
  audTag}` so cloudflared itself rejects any request lacking a valid Access token (no crypto added to the
  stdlib gate), then a catch-all 404.
- **Gate changes** (`gate.py`, with tests):
  - trust `Cf-Access-Authenticated-User-Email` (only reachable via cloudflared on the private network) and
    map it to the athlete through `users.json`: one sign-in instead of two. The Pushover-code login can be
    deleted, or kept as a fallback path for the Funnel during the overlap;
  - `GATE_PREFIX` becomes optional/empty (the secret prefix is no longer what hides the login);
  - `same_origin()` and cookies re-checked under the new host; drop the `/desktop/` no-cookie route.
- **Strava connect for new riders** (`/strava/callback`, today a temporary Funnel path straight to the
  companion): change Strava's "Authorization Callback Domain" to the new domain (it takes one domain), and
  route the callback through a Worker that stores the one-time code for home to collect, same pattern as
  the webhook. Then the companion is never directly exposed, even during an invite. Simpler alternative:
  an Access "bypass" rule on that one path into the tunnel, switched on only while an invite is open.
- `notifyd`: `PORTAL_PUBLIC` → new host (links in pushes and emails).

**What changes for riders (the migration cost)**
- New address. Browser-stored things are per-address and do not carry over: player settings, last world,
  sim flags, the 26 MB offline world cache, and **any ride still waiting to upload**. Before cutover the
  old page must show "upload pending rides first" and then redirect.
- Desktop app (Corbin, Brad): the address is baked into the installer or saved in `config.json`, and the
  update feed is derived from it. Ship shell 0.1.5 **through the old feed** with: new baked address, a
  one-time rewrite of a saved ts.net address, feed at `dl.<domain>`. Only then cut over.
- A code by email about once a month per device. **Risk to test:** a session expiring mid-ride. API calls
  would start getting a redirect to the Access login instead of JSON. The ride itself is safe (uploads
  queue offline already), but the page needs to recognise it and ask for a re-login after the ride rather
  than fail silently. Also test: the group-ride websocket staying up across expiry, the Electron window
  completing the Access login, the service worker under Access, 4 MB ride uploads.
- **Overlap:** run both addresses for two weeks. The Funnel gate answers every page with a redirect notice
  once pending uploads are zero.

### 4.4 Funnel off (last)

When no rider has touched the old address for two weeks: remove the three paths, stop and disable
`trainermax-gate.service`, `tailscale funnel reset`, revoke the node's funnel attribute in the tailnet
policy, update `homelab-exposure-posture` (WAN = WireGuard only; Cloudflare tunnel outbound).

## 5. Risks and how each is held

| Risk | Held by |
|---|---|
| Cloudflare account takeover | hardware-key 2FA; local tunnel config; tunnel container can reach only the gate; scoped tokens |
| Access misconfigured open | cloudflared `access.required` rejects unsigned requests; gate still enforces per-athlete rules |
| Cloudflare outage | Portal and car audio down until it returns (Funnel has the same single-provider shape today) |
| Free-tier limit hit | Hard stop until 00:00 UTC, no bill. Our volumes are a tiny fraction (5 riders; 1–2 cars) |
| Lost webhook events (queue 24 h, cutover gap) | Events are hints; importer catch-up (verify it exists and its period) |
| Ride lost in the address change | Pending-upload check before redirect; two-week overlap |
| Plaintext at Cloudflare | Accepted or not by Brad (§7 Q1); no medical detail is stored by design already |

## 6. Cost and effort

- Money: about $10.46/yr for the domain. Workers, Durable Objects, Queues, Pages, Access (≤50 users), Tunnel
  and R2 at this size: $0.
- Effort: phase 0 one hour (Brad); webhook half a day; portal 2–3 days including the shell release and
  testing with a real rider; car audio as its own plan.

## 7. Decisions needed from Brad

1. **Cloudflare seeing Trainer Max traffic in the clear:** acceptable? If not, keep the portal on Funnel
   (it is fine there) and use Cloudflare only for car audio and the webhook. That is a perfectly good
   end state.
2. **Monthly email code vs today's never-sign-in-again:** OK for Rachel and Corbin? (Alternative: Tunnel
   without Access, keep the gate's own login and secret prefix. You get the hidden origin, edge logs and
   rate limits, but pre-login traffic still reaches the gate.)
3. **Domain name**, and whether `coach.` / `lights.` / `hooks.` / `dl.` suit.
4. **Timing:** car audio now; webhook any time; portal in January?
5. **Card on file** for R2.

## Sources

- Access session range: https://developers.cloudflare.com/cloudflare-one/identity/users/session-management
- Access free plan (50 users): https://www.cloudflare.com/plans/sase-zero-trust.md
- cloudflared `originRequest.access`: https://developers.cloudflare.com/tunnel/advanced/origin-parameters
- Local tunnel config: https://developers.cloudflare.com/tunnel/advanced/local-management/configuration-file/index.md
- Large files / video terms: https://developers.cloudflare.com/fundamentals/reference/policies-compliances/delivering-videos-with-cloudflare/ and https://blog.cloudflare.com/updated-tos
- Queues on the free plan: https://developers.cloudflare.com/changelog/2026-02-04-queues-free-plan/ and https://developers.cloudflare.com/queues/pricing/
- Durable Objects free tier: https://developers.cloudflare.com/changelog/2025-04-07-durable-objects-free-tier
- Registrar pricing: https://www.cloudflare.com/application-services/products/registrar/buy-com-domains
- Strava webhooks (one subscription per app): https://developers.strava.com/docs/webhooks/

Not verified, check at build time: the 100 MB request-body cap on the free plan (our largest upload is
4 MB); how Access answers fetch/XHR after a session expires; websocket behaviour across Access expiry.
