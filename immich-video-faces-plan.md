# Immich Video Face-Tagging Sidecar — Plan

Immich only runs facial recognition on a video's **thumbnail frame**, so people who
appear mid-video are never tagged. Plan: a sidecar service on the Beelink that samples
frames from each video, runs them through **Immich's own ML pipeline** (same model, same
embedding space), matches against existing named people, and writes faces back through
the official API so they appear in the real People view/search.

Scope decision: **known people only** (the 11 named persons). Unknown faces are dropped —
the write-back API requires a `personId`, which is also our cheapest false-positive gate.

## Verified facts (live-tested against the running stack, 2026-07-20)

- **Immich v2.1.0** on the Beelink, `http://192.168.10.217:2283`. Library: 18,629 images,
  **4,132 videos**, 11 named people, 12,230 face embeddings.
- **Write-back API exists:** `POST /api/faces` with
  `{assetId, personId, imageWidth, imageHeight, x, y, width, height}` (all required).
  This is the endpoint the UI's manual face-tagging uses. Faces land in `asset_face`
  with `sourceType='manual'` (enum: `machine-learning | exif | manual`).
- **ML endpoint tested working:** `immich_machine_learning` (openvino build, container
  IP 10.67.0.3 on the `immich_default` docker network, port 3003, **not published to
  host**). `POST /predict` multipart:
  - `entries={"facial-recognition":{"detection":{"modelName":"buffalo_l","options":{"minScore":0.7}},"recognition":{"modelName":"buffalo_l"}}}`
  - `image=@frame.jpg`
  - Returns per-face `boundingBox {x1,y1,x2,y2}` + 512-d `embedding` (JSON-encoded array).
- **Live recognition config** (`GET /api/system-config`): model `buffalo_l`,
  `minScore 0.7`, `maxDistance 0.5`, `minFaces 3`.
- **Embeddings queryable:** `face_search(faceId, embedding vector(512))` with a
  vchordrq cosine index (`vector_cosine_ops`); joins to `asset_face(personId, sourceType)`.
  DB user/db = `immich/immich`; postgres also not published to host — sidecar joins the
  `immich_default` network. **DB is read-only for us; all writes go through the API.**
- API key: use a dedicated admin-scoped key stored at
  `/home/pi/cecret_lake/immich_video_faces/api_key` (create at setup; don't reuse the
  slideshow key — it's per-user for library reads, and we want revocability).

## Pipeline (per video)

1. **Skip check** — sidecar sqlite has the assetId? Already done. (Also skip if the
   asset already has faces for that person from a prior run.)
2. **Fetch** — download original via `GET /api/assets/{id}/original` (or read the
   library path directly; API is simpler and permission-safe).
3. **Extract frames** — ffmpeg ~1 fps, cap 60 frames, scaled to max 1280px long edge.
   Use `-vf "fps=1,thumbnail"`-style or `select` with a sharpness bias so we feed sharp
   frames, not motion blur. ffmpeg handles rotation metadata → frames come out
   display-oriented.
4. **Detect + embed** — POST each frame to `/predict` (pipeline above). Beelink iGPU via
   openvino; ~100–300 ms/frame.
5. **Match** — for each detected face, kNN against existing embeddings in Postgres
   (`ORDER BY embedding <=> $1 LIMIT 5`, join to `asset_face.personId`,
   `sourceType='machine-learning'` only so we never match against our own inserted
   faces). Nearest named person wins if within threshold.
6. **Vote** — aggregate per person across the video's frames (gates below).
7. **Write back** — one `POST /api/faces` per (person, video): the best-scoring frame's
   box, with that frame's dimensions as `imageWidth/imageHeight` (same aspect as the
   preview, so the UI overlay scales correctly).
8. **Record** — mark assetId done in sidecar sqlite with per-person distances (for
   later threshold tuning / audit page if wanted).

## False-positive gates (the "ghosts" question)

More frames = more raw detections, but aggregation flips the math in our favor:

- **Detection gate:** `minScore ≥ 0.7` (Immich's own) + minimum face size ~48px —
  drops tiny background faces and most pattern-ghosts.
- **Recognition gate:** cosine distance **≤ 0.45** — deliberately *stricter* than
  Immich's 0.5 because video frames are lower quality than photos. Ghosts that survive
  detection almost never land this close to a named person; they become unmatched faces,
  which we drop by construction.
- **k-of-n vote:** person must match in **≥2 distinct frames** (≥3 for videos with >30
  sampled frames) before tagging. Kills one-frame blur flukes.
- **Look-alike risk** (siblings / kids across ages) is the real failure mode, not ghosts.
  If a face's top-2 nearest *people* are within 0.1 distance of each other, treat as
  ambiguous and skip. Tune against the audit log after the first batch.

## Phase 0 results (2026-07-20 — PASSED, repo `/home/pi/immich-video-faces`)

Spike ran end-to-end on test asset `7bd2b030` (39s, Simon clear + Claire not head-on):

- **Simon: 14 frame hits** (best d=0.212), **Claire: 3 hits** (best d=0.346) — both
  tagged, matching expectations exactly (Claire borderline but clears the 2-frame vote).
- **Rerun survival PASSED:** both `sourceType='manual'` faces intact after a
  `refresh-faces` job re-run on the asset.
- **Big architectural finding — person clusters are per-user.** Two Simons/Claires
  exist (brad's + adrienne's accounts). kNN must be scoped to the asset owner's
  people (`p."ownerId" = asset.ownerId`) and writes must use a key belonging to that
  user, else every strong match "collides" with the other account's cluster of the
  same kid and the ambiguity gate kills it. Videos split: adrienne 1,627 / brad 2,505.
- Keys: adrienne scoped key created (cecret_lake/immich_video_faces/api_key).
  Brad still needs one — his slideshow key has `face.create` (usable interim) but
  not `apiKey.create`; mint a scoped key with his `{all}` key or from the UI.
- Ambiguity gate observed working on real sibling confusion (4 faces skipped where
  Claire-vs-Simon distances were within 0.10).

## Preflight tests (Phase 0 — do these before writing the batch runner)

1. **Rerun survival:** API-tag one video, then re-run FACE_DETECTION on that asset from
   the admin Jobs page. Confirm the `sourceType='manual'` face survives (it should —
   the detection job only clears `machine-learning` faces — but verify before tagging
   4k videos).
2. **UI sanity:** confirm the tagged person shows on the video in the People view and
   that the face box overlay isn't absurd on the preview.
3. **Threshold spot-check:** run 10 known videos (family present) + 5 videos with
   strangers/crowds; eyeball precision at 0.45 before batch.

## Resource guardrails (Beelink = 4-core N100, baseline load ~1.5)

The load has two sources, and a docker CPU cap only reaches one of them:

1. **Sidecar (ffmpeg decode + orchestration)** — cap directly in compose:
   `cpus: "1.5"` (hard ceiling) **plus** `cpu_shares: 256` (soft priority: under
   contention the kernel gives HA/Immich/dashboard ~8× our weight; when the box is
   idle we still get the full 1.5). Also `ffmpeg -threads 2`, and write extracted
   frames to tmpfs (`/dev/shm`, they're small and transient) to avoid disk churn.
2. **`immich_machine_learning` inference** — do NOT cap the container itself (it
   serves live Immich: uploads, smart search). Control it from the demand side:
   **strictly one in-flight `/predict` request** (sequential frames, sequential
   videos), which bounds its extra load to roughly one worker; openvino puts most
   of the math on the iGPU anyway.

Plus two runner-level brakes:

- **Load-average guard:** between videos read `/proc/loadavg`; if 1-min > 2.5,
  sleep 60 s and re-check (resume < 2.0). Baseline is ~1.5, so this triggers only
  when something real is happening.
- **Night window:** batch cron runs 00:30–06:30 and exits at window end; the sqlite
  skip-log makes resumption free the next night.

## Phase 1 status (2026-07-20 — DEPLOYED)

- `tagger.py` (lib + single-video CLI) + `runner.py` (batch: sqlite `state.db`,
  per-owner key map, loadavg pause >2.5/resume <2.0, `--window`, `--limit`,
  `--dry-run`, per-asset error capture). Long videos spread the 60-frame budget
  across the full duration instead of only sampling the first minute.
- **Backlog is actually 727 videos, not 4,132** — the other 3,405 are *hidden*
  Pixel motion-photo fragments (visibility='hidden'), deliberately excluded via
  `search/metadata` (their still halves already got photo face recognition).
  Split: adrienne 196 / brad 531. Realistic backfill: ~1 night.
- Brad's scoped key created via UI → `cecret_lake/immich_video_faces/api_key_brad`
  (file was root:644; asked user to chmod 600/chown pi).
- Nightly cron installed in pi's crontab on the Beelink:
  `30 0 * * * … runner.py --window 00:30-06:30 >> logs/nightly.log`.
  This doubles as Phase 2 steady-state — after backfill it tags new videos nightly.
- Idempotency verified in the wild: smoke batch hit videos whose thumbnails had
  already been recognized (Simon/Claire) and correctly skipped re-tagging.

## Phases

- **Phase 0 — spike (half a day):** single script, one video end-to-end + the three
  preflight tests above.
- **Phase 1 — backlog batch:** run the 4,132 videos inside the guardrails above.
  At ~10–20 s/video plus pacing that's ~15–25 h of churn → expect **2–3 nights**
  in the 00:30–06:30 window; that's fine, it's a one-time backfill. Write an audit
  summary (videos tagged, faces added per person, ambiguous-skip count).
- **Phase 2 — steady state:** small container on `immich_default` network + hourly cron
  (or reuse the slideshow sync loop's pattern): query recent VIDEO assets via
  `POST /api/search/metadata`, process new ones. New repo `/home/pi/immich-video-faces`,
  git-backed per homelab convention (secrets stay in cecret_lake, referenced by path).

## Non-goals / retirement

- No new-person discovery, no LLM involvement, no separate face stack (embeddings must
  live in buffalo_l's space to be comparable — that's *why* we call Immich's own ML).
- GX10 not needed; Beelink openvino is plenty for this volume.
- Native video face recognition is a long-standing Immich feature request. If it ships,
  the sidecar retires cleanly: we used their models, their person IDs, and their API —
  worst case, delete our `sourceType='manual'` faces by query and let native take over.

## Open questions

- Does `POST /api/faces` trigger any person-thumbnail regeneration or clustering side
  effects? (Observe during Phase 0.)
- HDR/rotated video edge cases — verify a portrait phone video's box lands right.
- Whether to also tag `person.faceAssetId`-style feature faces — no; leave person cover
  faces alone.
