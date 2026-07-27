# Self-Hosted F-Droid Repo (Silent App Updates)

Set up 2026-07-17. Purpose: family phones get updates to the internal Android
apps automatically in the background — no download links, no reminders — the
same way Play Store apps update overnight.

## How It Works

- `fdroidserver` (2.4.5, venv install — no root) maintains a signed repo at
  `/home/pi/fdroid`. The public half is copied to `/home/pi/apks/fdroid/repo`,
  which the Home Services dashboard serves. Phones use the HTTPS front
  (modern F-Droid clients reject plain-HTTP repo URLs — that's why this
  exists; see `/home/pi/fdroid-https/README.md`):

  ```text
  https://fdroid.illuminatehealthanalytics.com/apk/fdroid/repo
  ```

  The hostname is a Cloudflare DNS record pointing at `192.168.10.217`
  (LAN-only, not internet-reachable); a Caddy container on the Beelink
  terminates TLS with an auto-renewing Let's Encrypt cert (DNS-01) and
  proxies `/apk/*` to the homepage container. The old
  `http://192.168.10.217:3000/apk/fdroid/repo` URL still serves but phones
  won't accept it.

  Repo signing-key fingerprint (shown by phones when adding, for verification):

  ```text
  3C30DABF70059846BBB965220F86C824992061EF6C60E428F40F70FC461AE6BD
  ```

- Phones run **F-Droid Basic** with this repo added. On Android 12+, F-Droid
  Basic silently auto-updates any app **it installed** (unattended-update API,
  same one the Play Store uses). Apps installed some other way (link download,
  adb) must be reinstalled once through F-Droid Basic to become eligible.

- Every app's Gradle config now derives `versionCode` from build-time epoch
  seconds, so every rebuild is automatically a higher version. No manual
  version bumps needed. (Epoch seconds overflow int32 in 2038.)

## Layout

```text
/home/pi/fdroid/config.yml    repo settings + keystore passwords — SECRET, never serve or commit
/home/pi/fdroid/keystore.p12  repo index signing key — SECRET (in nightly homelab backup)
/home/pi/fdroid/publish.sh    one-command publish (see below)
/home/pi/fdroid/repo/         canonical repo: APKs + signed index
/home/pi/fdroid/venv/         fdroidserver install
/home/pi/apks/fdroid/repo/    web-served copy (rsync'd by publish.sh)
/home/pi/apks/fdroid/qr.png   QR of the repo URL for phone enrollment
/home/pi/apks/fdroid/index.html  enrollment landing page (homepage tile links here —
                              the bare repo URL crashes the dashboard's Next.js
                              router if opened in a browser; it's for F-Droid only)
```

The repo signing key only signs the index (repo identity on phones). Losing it
means re-adding the repo on each phone — annoying, not fatal. App signing keys
are separate; see `android-apk-publishing-guide.md`. Both are in the nightly
homelab config backup (`explicit_includes` in
`/home/pi/scripts/homelab_backup/config.json`).

## Publishing an Update

Build the app as usual (see `android-apk-publishing-guide.md` for per-app
build commands), then:

```bash
/home/pi/fdroid/publish.sh <built-apk> <served-name>

# example
/home/pi/fdroid/publish.sh \
  /home/pi/android-stt/app/build/outputs/apk/debug/app-debug.apk \
  android-stt-latest.apk
```

The script:

1. Refuses to publish if the APK's `versionCode` is not strictly greater than
   the published one, or if the package id doesn't match (wrong served-name).
   A fresh rebuild always has a higher code — a refusal means you passed a
   stale APK.
2. Copies the APK to both `/home/pi/apks/<name>` (legacy homepage direct link)
   and the F-Droid repo, regenerates the signed index, rsyncs to the web dir,
   and curl-checks the served index.

Phones then pick up the update on F-Droid Basic's next background sync
(roughly hourly to daily depending on Android's job scheduling; instant if you
open the app and pull to refresh).

Served names (same as before): `tts-router-latest.apk`, `home-agent-latest.apk`,
`antennapod-v2-latest.apk`, `tempo-latest.apk`, `voice-notes-latest.apk`,
`trainer-max-latest.apk`, `android-stt-latest.apk`.

## Phone Enrollment (One-Time Per Phone)

1. Install **F-Droid Basic** (from f-droid.org, or sideload its APK — it can
   thereafter update itself). Not classic F-Droid: Basic is the variant with
   unattended updates.
2. Open the homepage "F-Droid Repo" tile (`/apk/fdroid/index.html`) on the
   phone and tap "Add repo in F-Droid Basic" (an `fdroidrepo://` link), or in
   F-Droid Basic: Settings → Repositories → `+` → scan the QR on that page.
   Optionally disable the default f-droid.org repos to keep the app list
   family-only.
3. For each app already on the phone: it appears with an "Update" available
   (repo versionCodes are far above the installed ones). Updating through
   F-Droid Basic makes it the installer of record. Signatures match the
   installed builds, so data is preserved — not an uninstall/reinstall.
4. Settings → enable unattended/automatic updates (and "over Wi-Fi" is fine —
   phones are home most nights).

After step 4, publishes flow to the phone with zero interaction. Note: LAN
only — updates sync when the phone is on home Wi-Fi (or Tailscale if the
phone's VPN routes to 192.168.10.217). The repo hostname resolves to a
private IP, so it only works from inside the network.

## Gotchas

- **New file paths 404 until homepage restarts — legacy LAN links only.**
  The homepage's Next.js only serves public files that existed at its startup.
  The HTTPS repo URL is immune (Caddy serves `/home/pi/apks` straight from
  disk — this was mandatory, since every publish creates a brand-new
  `diff/<timestamp>.json` that phones fetch for incremental index updates).
  Only when adding a brand-new filename under the old
  `http://192.168.10.217:3000/apk/...` links:
  `cd /home/pi/homepage && docker compose restart homepage`.
- **Never regenerate `keystore.p12`** — phones would reject the repo until
  re-added.
- **Don't downgrade versionCodes.** Epoch-seconds codes (~1.79B) are now live
  on phones; reverting an app's Gradle to a small fixed versionCode would make
  every future build look like a downgrade. For the forks (AntennaPod, Tempo),
  upstream's own versionCodes are permanently below ours — irrelevant while we
  rebuild from source, but a stock upstream APK can no longer be installed
  over ours.
- `fdroid update` leaves per-app skeletons in `/home/pi/fdroid/metadata/`;
  editable for nicer names/descriptions in the client, but app labels from the
  APKs are what mostly show, so it's optional.
- Windows Transcribe is an `.exe` — stays a plain homepage download link.
