# DRAFT — not posted. Target: https://github.com/immich-app/immich/issues (Bug report, mobile)

**Title:** [Android] Background process grows ~70 MB/h while idle (5 GB after 3 days), fills swap and lags the whole phone

### The bug

With automatic background backup enabled and battery usage set to Unrestricted, the
`app.alextran.immich` process stays alive in the background permanently and its memory grows
steadily while idle, about 60–90 MB per hour. It never shrinks until the process is killed. After a few days
it holds most of the phone's zram swap. Android's low-memory killer then kills every other
background app instead of Immich, and the phone lags badly when woken: 6,000+ major page
faults/s, app launches of up to 8 s, and ANRs in other apps.

Immich isn't killed because its content-observer job (WorkManager `MediaObserver`) keeps
waking it, so it always counts as a recently used cached process (oom_score_adj ~915–945).
Other apps sit at 985–995 and get killed first.

### Measurements

Sampled every 5 min over adb from `/proc/<pid>/status` (VmRSS + VmSwap) by a logger, starting
from a fresh process:

| Time (MT)        | RSS MB | Swap MB | Total MB |
|------------------|-------:|--------:|---------:|
| Sun 11-08 19:00  |   251  |    314  |    565   |
| Mon 11-09 07:00  |   264  |  1,169  |  1,433   |
| Mon 11-09 19:00  |   427  |  2,061  |  2,488   |
| Tue 11-10 07:00  |   520  |  2,650  |  3,170   |
| Tue 11-10 19:00  |   658  |  3,195  |  3,853   |
| Wed 11-11 07:00  | 1,041  |  3,641  |  4,683   |
| Wed 11-11 13:00  | 1,114  |  3,926  |  5,040   |

- During this window the app was on screen for only ~3 minutes, and it uploaded **one** photo (1.5 MB).
  The growth isn't tied to uploads; it happens while idle in the background.
- A previous run, Nov 4–6 after a phone reboot, showed the same pattern: 281 MB → 2.4 GB in 33 h.
- `am kill app.alextran.immich` immediately frees the memory (swap free 4 MB → 3.1 GB) and the lag disappears.
  But after a kill, the next photo isn't uploaded until the app is opened again: the MediaObserver
  job starts a fresh process, Android freezes it about 10 s later, and nothing is uploaded. So
  killing it isn't a usable workaround for instant backup.

### Expected

The background process should either release memory between backup runs, or exit and let
WorkManager start it on demand, so that memory doesn't grow without bound.

### Steps to reproduce

1. Enable background backup and set Immich's battery usage to Unrestricted.
2. Leave the phone in normal use for 1–3 days without opening Immich.
3. Watch `adb shell grep -E 'VmRSS|VmSwap' /proc/$(pidof app.alextran.immich)/status`.

### Platform

- Phone: Pixel 8 Pro, Android 17 (build CP3A.261005.005), 12 GB RAM + ~5.9 GB zram
- Immich Android app: 3.3.4 (installed 2026-10-21; from that point the phone "started lagging lately")
- Server: immich-server 2.1.0 (`release` tag), Docker
- Library: ~4,000 assets backed up from this device

### Logs

<!-- TODO before posting: attach a logcat slice from the Immich pid, and the app's in-app logs
     (Settings → Advanced → Logs) covering a few hours of growth. -->
