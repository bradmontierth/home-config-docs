# Podcast Pipeline Guide

This guide is the high-level home lab note for the podcast app in:

```bash
cd /home/pi/podcast
```

Use the repo README and scripts for implementation details. This note is meant to give agents and other users the mental model, service boundaries, and operational entry points.

## Device roles

The podcast app runs primarily on this Raspberry Pi / Linux host.

The Jetson is used only for GPU-heavy work. CPU-heavy or I/O-heavy work stays local on this machine.

```text
Podcast host / this repo
        |
        | local CPU work
        | - ABS access
        | - downloading audio
        | - HLS/MP3 handling
        | - ffmpeg normalization
        | - queue markers
        | - chapter prompts
        | - publishing feeds/static files
        v
Jetson worker over LAN REST
        |
        | GPU work only
        | - Parakeet ASR
        | - pyannote diarization
        | - resident/service control
```

## Main repo

Primary repo:

```bash
/home/pi/podcast
```

Important docs inside that repo:

- `/home/pi/podcast/README.md`
- `/home/pi/podcast/jetson_worker/README.md`
- `/home/pi/podcast/JETSON_STACK_DEPENDENCIES.md`

## Pipeline flow

The normal processing flow is:

```text
ABS podcast item
        |
        v
local download + ffmpeg normalization
        |
        v
Jetson Parakeet ASR job
        |
        v
transcript txt/json/vtt
        |
        v
chapterization through OpenRouter / Gemini
        |
        v
optional Jetson diarization
        |
        v
feedwriter publish
        |
        v
static RSS/media files
```

## Queue markers

The app uses marker files under the repo queue directory to move episodes through the pipeline.

Common states:

- `.ready`: transcript is ready for chapters
- `.chapterized`: chapters are done and diarization is needed
- `.done`: ready for feedwriter publication
- `.published`: feedwriter published it
- `.error`, `.missing`, `.diarize_error`: failure/retry states

Do not assume the Jetson owns the whole pipeline. The Jetson owns GPU job execution; this repo owns the episode pipeline and queue markers.

## Important commands

From `/home/pi/podcast`:

```bash
./bin/deploy_jetson_worker.sh
```

Deploys/restarts the Jetson REST worker.

```bash
bin/transcribe_nightly.sh
```

Downloads/normalizes recent ABS episodes locally and submits Parakeet jobs to the Jetson worker.

```bash
./run.sh bash bin/process_queue_chapters.sh
```

Processes `.ready` transcript markers into chapters.

```bash
./run.sh bash bin/process_queue_diarization.sh
```

Processes `.chapterized` markers through Jetson diarization.

```bash
./run.sh docker compose up -d feedwriter static
```

Runs the publisher and static file server.

## Published outputs

Feedwriter publishes RSS/media/static assets from this repo.

Useful endpoints depend on `PUBLIC_BASE_URL`, but the repo README documents the current feed URLs and OPML/static service pattern.

## Secrets

OpenRouter credentials are intentionally not stored directly in the repo `.env`.

The launcher reads the OpenRouter key from:

```text
/home/pi/cecret_lake/openrouter/.env.key
```

Avoid printing environment variables or process environments in logs because they may contain keys.

## Mental model

Treat the podcast app as a local batch pipeline with a remote GPU worker:

- Local host is the source of truth for episodes, queue state, transcripts, chapters, publishing, and feed output.
- Jetson is the source of truth for GPU job queue state and resident GPU service state.
- Other apps should call the Jetson REST API directly for GPU services such as Parakeet, but they should not touch this repo's queue unless they are participating in the podcast publishing pipeline.
