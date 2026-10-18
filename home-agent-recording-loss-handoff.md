# Home Agent app — lost dictation recordings (2026-10-18) — RESOLVED

Brad's two long voice monologues failed with
`[transcription error] The last recording is no longer available.`

## Root cause (confirmed 2026-10-18 via wireless ADB + run-as)

- The phone's storage is **100% full** (654 MB free of 110 GB on /data).
- The app recorded into `context.cacheDir`. Android clears app caches when
  storage is critically low; the cache dir's mtime (08:31) matches the loss.
  `cache/` was empty — **the two recordings are gone, nothing to recover.**
- Gateway had no failed `/api/transcribe` ever: the audio never left the phone.

## Fix shipped (commit 10035a3, versionCode 1792334085, published to F-Droid
## and `adb install`-ed directly 2026-10-18 08:38)

- Recordings go to `filesDir/recordings/dictation-<timestamp>.m4a` (never
  cache-cleared); deleted only after a delivered transcript; pruned after 7 days.
- `RecordingService`: `foregroundServiceType="microphone"` + partial wake lock
  while recording (`FOREGROUND_SERVICE_MICROPHONE` permission in the manifest).
- `RecorderState` is a process-scoped `object`, not `remember {}`.
- On launch the app lists untranscribed recordings in the terminal and the
  Retry / "Transcribe saved (n)" button sends them oldest first.
- Missing-file / failed-`stop()` paths name the kept file.

## Verified on the phone over ADB

| Test | Result |
| --- | --- |
| Dictate → Finish | FGS up (`types=0x80`), file in `files/recordings/`, transcribe 200, file deleted, service gone |
| Screen locked (Dozing) 65 s mid-recording | file grew 8 → 106 KB, service still foreground; Brad's speech while locked is in the transcript |
| `am force-stop` mid-recording | 170 KB file survived, ffprobe reads 104.9 s, gateway transcribed it fine |

Pending on Brad: unlock the phone — the app is open with that 170 KB test
recording listed as saved; tap **Retry transcribe** to see the recovery UI
work end to end (it then deletes the file).

## Still open

- **Phone storage at 100%.** Free space; this will keep breaking other apps.
- Wireless ADB pairing from the beelink to the phone (192.168.10.38) now
  persists; connect with `adb connect 192.168.10.38:<port from adb mdns services>`.
