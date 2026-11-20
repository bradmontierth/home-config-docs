# SMS Spam Gate (Fossify Messages fork + local LLM screen)

Status: BUILDING 2026-10-21. Repo `/home/pi/sms-gate` (Fossify fork, branch `main`, pushed to private GitHub sms-gate; remote `upstream` = Fossify;
`sidecar/` inside). Sidecar LIVE on the beelink :8809 (docker, review page screenshot-checked);
APK PUBLISHED 2026-10-21 (sms-gate-latest.apk, F-Droid repo; vc 1792603979 adds Settings → Spam gate →
"Screen existing conversations" with a 30/90/365-day/all window: unreplied non-contact threads go through the
same gate, flagged ones marked read + archived). Brad INSTALLED 10-21, ran the 30-day backfill, "classifier does a great job". FOLDERS built same day
(All/Chats/Updates/Codes/Spam chips over the list, per-thread store in SharedPreferences, reply ⇒ Chats,
Codes auto-archive 24 h) — on-phone check pending. Brad set it as the DEFAULT SMS app 10-21. Committed + pushed 4b018bf. Feasibility in "Verified".

## Problem

Election-season spam SMS on Brad's Pixel, several per day, each from a fresh
number. Marking spam / blocking in Google Messages does nothing for the next
sender. Goal: unknown senders are screened by the house LLM before the phone
is allowed to buzz. Known people always get through.

## Decision

Replace Google Messages as the default SMS app with a fork of **Fossify
Messages** (GPL-3, Kotlin, actively maintained, 1.9.1 July 2026) and add a
screening step in its receive path. This is the only route that stops a text
*before* it notifies; a notification-listener approach would keep RCS but
can't prevent the first buzz without silencing Messages entirely.

Cost accepted by Brad 2026-10-21: RCS is lost (family + Adrienne are on
WhatsApp; the few RCS threads fall back to SMS/MMS).

## Architecture

```
Pixel (Fossify fork, default SMS app)
  SmsReceiver.onReceive
    ├─ existing: keyword filter, blocked numbers           → drop (unchanged)
    ├─ NEW gate.decide(address, body):
    │    known sender?  (in contacts, OR thread has an outgoing message)   → NOTIFY, no network
    │    OTP / verification-code regex                                      → NOTIFY, no network
    │    short-code sender + no URL? (tunable, maybe skip)                  → NOTIFY
    │    else POST /screen to the sidecar (timeout ~6 s)                    → sidecar verdict
    │    network error / timeout                                            → NOTIFY (fail open)
    └─ handleMessageSync(..., notify = verdict)
         notify=true : as today
         notify=false: insert message, mark read, ARCHIVE the thread, no notification
                       ("Archived" in the app = the quarantine folder)

Beelink sidecar  `sms-gate`  (FastAPI, docker, :8809)
  POST /screen {from, body, device}  → {notify, category, confidence, reason}
     - normalises, applies cheap rules (lookalike-domain list, STOP-to-opt-out, etc.)
     - calls GX10 qwen3-next :8095 with json_schema constrained output,
       enable_thinking=false, temperature 0, short-reason instruction
     - logs every decision to sqlite (from, body, verdict, latency, model raw)
  GET  /            review page: everything screened, filter by verdict,
                    "wrong" button flips the label → becomes a few-shot example
  GET  /examples    the current few-shot set (editable file, hot-reloaded)
```

Why a sidecar instead of the phone calling the GX10 directly: prompt and
few-shot iteration without an APK rebuild, a decision log, the review page,
and one place to swap models later. Same pattern as voice-ops (:8787).

Phone → beelink path: LAN IP over WireGuard when away (Tempo's
`com.wireguard.android.action.SET_TUNNEL_UP` auto-connect pattern,
voice-assistant-plan.md ~L790, can be reused). Off-tunnel = fail open.

## Classification contract

Schema (verified against :8095):

```json
{"category": "personal|transactional|political|scam_phishing|romance_bait|marketing|other_spam",
 "notify": bool, "confidence": number, "reason": "<=120 chars"}
```

App only acts on `notify`. Sidecar can apply a confidence floor
(e.g. suppress only when `notify=false` and `confidence>=0.8`).

Prompt stance: the question is "is this a real person or a transaction the
owner would want?", not "is this spam?". Unsure → notify. Bias every error
toward a spam buzz rather than a missed plumber / school / pharmacy.

## Verified 2026-10-21

- qwen3-next :8095 with `response_format: json_schema`: 8 samples, 7 right.
  Political (two styles), romance bait, plumber, dentist reminder, Costco
  shipping, old friend all correct. **Miss:** fake-USPS phishing with a
  `.top` lookalike domain → "transactional, notify" (safe direction; fix with
  few-shot phishing examples + a lookalike-domain rule in the sidecar).
- Latency: 1.6–2.7 s per call with "reason under 15 words"; 4–6 s when the
  reason is unconstrained (first run also hit a JSON truncation at
  max_tokens=200 — keep reason short, max_tokens 400). Base model RTT 0.36 s.
- Fossify Messages receive path: `receivers/SmsReceiver.kt` does keyword
  filter → blocked-number → optional block-unknown → `handleMessageSync`
  (insert SMS, update conversation, `showReceivedMessageNotification`).
  Gate slots in right after the existing filters. MMS path is
  `receivers/MmsReceiver.kt` (`isAddressBlocked`/`isContentBlocked`
  overrides + `handleMmsMessage`); same gate, phase 2.
- Fossify already has: block list (import/export), block-unknown-senders,
  keyword filter, archive. Archive is the quarantine.
- Toolchain: AGP 9.4.1, Gradle 9.8.1, Kotlin 2.4.20, compileSdk 36,
  Java 17 target, flavors `foss`/`gplay`. Host has JDK 17 + 21 and
  platform android-36. `gradlew tasks` resolved clean with JDK 21 in 1m 51s
  (memory-capped scope), so Phase 0 is just `assembleFossDebug`.
- Phone apps already reach the beelink over WireGuard (Tempo auto-connect).
- Sidecar end-to-end with the 16-example few-shot set: 12/12 fresh samples right
  (pastor, church friend, KSL buyer, rental, Delta, mom's dinner all notify;
  gym promo, campaign, 'hey handsome', loan offer suppressed; fake USPS + fake
  Chase caught by the lookalike rule at 0 ms). Model calls ~1.0 s warm, ~2.2 s
  from the container cold.

## Phases

0. **Build stock Fossify Messages** on this host (foss debug, memory-capped
   per android-apk-publishing-guide.md). Prove the toolchain, install on the
   Pixel as a *non-default* app first to confirm it runs.
1. **Sidecar**: `/screen` + sqlite log + prompt with few-shot set seeded from
   real spam copied out of Google Messages' Spam folder (Brad pastes 10–20).
   Replay the samples; add the USPS/lookalike cases. Unit test the rules.
2. **App gate**: known-sender check, OTP regex, sidecar call with timeout,
   fail-open, `notify=false` → mark read + archive. Settings screen entries:
   sidecar URL, enable toggle, "screen MMS too" toggle. Log verdict in a
   local table so the message's quarantine reason shows in-thread later.
3. **Cutover**: set as default SMS app (Android asks; Google Messages
   demoted but keeps its history). Fossify imports existing SMS/MMS from the
   system provider automatically (they live in the Telephony provider, not
   in Google Messages). Watch the review page for a week.
4. **Review page** polish + "wrong" button → few-shot; MMS gate; publish via
   `/home/pi/fdroid/publish.sh` for silent updates. Adrienne's phone later
   if she wants it (needs classic F-Droid 1.23.2, see fdroid-repo memory).

## Open questions / risks

- **Lookalike-domain phishing** passes the model today. Rules first, model
  second: any URL whose host is not on a short allowlist of known brands and
  looks like `<brand>-<words>.<cheap tld>` → notify=false regardless.
- **Wear OS / Android Auto**: Fossify uses MessagingStyle notifications, so
  watch mirroring and Auto read-out should keep working; verify on cutover.
- **OTP regex must be tight**: 4–8 digit code + ("code"|"verification"|"OTP"|
  "passcode"), else political "Reply 1 to…" texts would slip through.
- **Short codes**: political uses 5–6 digit short codes too, so don't
  whitelist short codes outright; let the model see them.
- **Repo home**: fork at `/home/pi/sms-gate` (app + sidecar in one repo, or
  app repo + sidecar under home_config like flight-watch). Pushed to GitHub
  as the backup, per homelab-git-backing conventions. GPL-3 keeps it open.
- **Upstream sync**: keep the gate in a handful of files (`gate/` package +
  ~15 lines in each receiver) so rebasing on Fossify releases stays cheap.

## Sources

- https://github.com/FossifyOrg/Messages
- https://f-droid.org/packages/org.fossify.messages/
