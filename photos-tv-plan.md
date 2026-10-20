# Photos TV Plan (Immich on the TV)

Our own Android TV viewer for the Immich library, replacing the community
`nl.giejay.android.tv.immich` app (v6.1.0 on the Shield). Main complaint: its
UI (grey rectangles labelled with file names like `PXL_2025…`).

Repo: `/home/pi/photos-tv` (`server/` index service, `tv/` Android TV app
`com.local.photostv`, label "Photos").

## Decisions (Brad, 2026-10-19)

- **Separate app**, not a section of Media Center. Different backend, photos
  are memory-heavy on a 2 GB Shield, and the two update independently.
- **Read-only.** No favouriting, editing, uploads or face tagging.
- **Shield first**; later the downstairs Chromecast with Google TV (the
  projector). Keep the APK small and targets generic Android TV.
- **Trips are backlogged** (not in the MVP).
- **Style:** modern, clean, minimalist, big type, remote-first. Pills are
  allowed sparingly. The Media Center "no pills" rule was about matching old
  Plex; here the rule is just "avoid AI-app tropes" (no glow gradients,
  no bubbly everything, no sparkle icons).

## Facts measured 2026-10-19

- Immich v2.1.0 at `http://192.168.10.217:2283`; smart search (CLIP),
  facial recognition and reverse geocoding enabled.
- Library: 19,828 timeline assets = 19,045 images + 783 videos. Owners:
  Brad 17,318, Adrienne 2,510. Partner sharing is on: either account's key
  sees all of them through `POST /api/search/metadata`. Full sweep with
  people + exif: ~8 s per key.
- Mime types: JPEG 18,694, MP4 676, HEIC 268, MOV 94, PNG 77, AVI 10, GIF 5,
  3GP 2. 3,623 motion photos (`livePhotoVideoId`). 3,439 assets have no city.
  Favourites: 1, so there is no Favourites row.
- Years 2001–2026; densest 2018 (2,569), 2025 (2,030), 2026 (1,903).
- **People are per account**: Brad's "Simon" and Adrienne's "Simon" are
  different person ids. Filter by NAME, merged across both accounts. Named
  people: Simon 2,286, Brad 1,932, Adrienne 1,730, Claire 1,159, Troy 93,
  Rachel 75, Brady 50, Caroline 5, Jared 3.
- Compact catalog (id, date, type, duration, thumbhash, city/state, people,
  favourite) for all 19,828: 2.81 MB JSON, **1.07 MB gzipped**.
- Shield (192.168.40.67, IoT VLAN 40): panel 3840x2160 but the app UI is
  **1920x1080** (`wm size` override), so Immich's 1440 px preview is the right
  full-screen size. ~2 GB RAM, /data 862 MB free, Android 11, 32-bit ABI.
  It reaches Immich :2283 directly.

## Architecture

**Index service** (`server/`, FastAPI + SQLite, docker on the Beelink, port
8807, LAN only), same pattern as `immich-slideshow`:

- Holds the Immich keys; the TV never sees them. Keys are read-only scoped
  (`asset.read, asset.view, asset.download, timeline.read, person.read,
  album.read, memory.read`), one per account, in
  `/home/pi/cecret_lake/photos_tv/` (`immich_key_adrienne` minted 2026-10-19
  via her full key). Partner sharing makes one key enough; a Brad key can be
  added later as `immich_key_brad` (Immich UI only).
- Sweeps both accounts, dedupes by asset id, merges people by name and
  excludes archived/trashed/hidden assets and screenshots (slideshow
  heuristic).
- `GET /api/v1/catalog`: the whole library as one compact gzipped JSON. The
  TV caches it and does every filter locally (person, photos/videos, month),
  so screens never wait on the network.
- Proxies `thumbnail` / `preview` images, person faces and video (with Range
  passthrough so seeking works) from Immich.
- Smart search passthrough (`/api/v1/search?q=`), results limited to catalog
  ids.
- Auth: per-device token header `X-PT-Token`. MVP provisions over adb (like
  Media Center's `mc_token` extra). Later: QR pairing (TV shows QR + code,
  phone approves on a Beelink page, revocable per device).

**TV app** (`tv/`, Kotlin + Compose, Coil, Media3; toolchain identical to
Media Center, JDK 17, memory-capped Gradle):

- Left menu: Home, Library, People, Search, Settings.
- Home: On this day, recent months, People row.
- Library: thumbnail grid under month headers ("October 2026 · 214");
  Left from the first column opens a year/month jump rail.
- Filters: All / Photos / Videos plus a person picker; the active filter is
  always spelled out ("Simon · Videos").
- Viewer: Left/Right = prev/next (hold accelerates), Up = date/place/people,
  Down = filmstrip, OK = slideshow play/pause or video play/pause, Back = the
  grid with that item focused. Thumbhash blur shows instantly; neighbours
  prefetch.
- Video: Media3 direct play of the original; AVI etc. through Immich's
  transcoded playback.
- No file names anywhere. Captions: "Saturday, July 12, 2025 · Bear Lake,
  Utah".
- Bounded caches: Coil disk ~200 MB, memory ~20 %.

## Phases

0. **Spike** (in progress 2026-10-19): service with catalog + image/video
   proxy; TV grid of the real catalog + viewer + video; measure
   cold launch, scroll smoothness, preview quality, a Pixel HEVC and an HDR
   clip, voice input inside the app; screenshots from the Shield.
   - DONE: service live on :8807 (docker project `photos-tv`). Sweep 9-10 s,
     19,751 assets after exclusions, catalog 908 KB gzipped served in ~3 ms;
     thumbnail (webp, ~22 KB) ~40 ms, preview (1920x1440 JPEG, ~550 KB)
     ~26 ms, video Range -> 206, smart search ~130 ms. Shield reaches :8807.
   - DONE: TV app 0.1 (Library grid with month headers, year rail, All /
     Photos / Videos, people chips, viewer with captions + slideshow +
     inline video, smart search with voice) builds as a 2.2 MB release APK,
     installed + provisioned on the Shield.
   - DONE on the Shield 2026-10-19 (screenshots reviewed): cold launch to
     library 1.16-1.26 s (catalog net 118-189 ms + parse 164-294 ms; disk
     parse 274 ms); 40-row held scroll 4.75 % janky frames, p50 10 ms, p90
     13 ms, p99 23 ms; app PSS 123 MB. Filters, viewer captions, inline
     video (hardware decode) and smart search all work. Voice input works
     in-app (`RecognizerIntent` opens Google's overlay with our prompt).
   - FIXED same day: first-open scroll clipped the title; blur placeholder
     peeked out beside photos (frame now sized to the photo's real ratio);
     catalog version changed every sweep so the TV re-downloaded it (now
     content-hashed; unchanged library answers 304).
   - People faces: Brad's keys cannot mint keys and Immich only allows
     `POST /api/sessions` from a session token, so no Brad key was made.
     Not needed: partner-visible asset faces carry bounding boxes (preview
     coordinates), so the service crops each person's biggest face from
     their 40 newest photos (current age, better than Immich's cluster
     thumbnails); Immich's cluster thumbnail is the fallback.
   - Gotcha: an asleep Shield (`mWakefulness=Asleep`) puts app uids on
     REJECT_ALL, so app sockets time out while adb-shell `nc` works. Not
     the firewall. Waking it turns the TV on over CEC.
   - Seen for phase 2: deep in the grid there is no "where am I" (month
     header scrolled off, rail doesn't mark the current year); the library
     has lots of utility shots (receipts, recipe pages, RAM sticks,
     whiteboards) that a "hide documents" option could filter via CLIP.
   - Family test 2026-10-19 evening (~30 min with the kids): "way better
     than the community app", fast. Three bugs reported, fixed in ad1d5a8:
     viewer direction (decision: the viewer follows TIME, Right = newer,
     slideshow + video-to-video advance newer; the grid stays newest-first
     in reading order); grid "bounce" (Compose's TV default pivot 30 % +
     spring and a 7 % focus zoom -> 42 % pivot, 220 ms ease, 4 % zoom);
     title/tabs cut off on the first row (same pivot cause). Header
     decision: no frozen full header (it would cost a quarter of the
     screen); the title shows fully on the first rows, and a slim pinned
     bar (month of the focused photo + active filter) appears once it
     scrolls away. Year rail highlights the current year.
     Repo committed (c30b798, ad1d5a8), remote
     `github-illuminate:bradmontierth/photos-tv.git` set, not pushed.
1. Service hardening (docker, systemd-free restart policy, delta sync) and
   adb provisioning.
2. Real screens: Home, Library + jump rail, filters, People, viewer, video.
3. Smart search (voice + keyboard), On this day, screensaver (DreamService).
4. Polish after family review on the TV; QR pairing; projector Google TV.

## Backlog

- Trips (consecutive days away from home, named by most common place).
- True 4K stills on a SurfaceView, only if the spike shows a clear win.
- Motion-photo playback on OK.
- Albums row if anyone starts using albums.
