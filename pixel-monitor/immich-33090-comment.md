Same bug here, independently: Pixel 8 Pro, Android 17 (CP3A.261005.005), app 3.3.4, background backup on, battery Unrestricted. One difference that may help narrow it down: my **server is v2.1.0**, so this doesn't depend on the server version.

I've been logging the process every 5 minutes over adb (`/proc/<pid>/status` VmRSS + VmSwap, no `dumpsys meminfo`). Here's one process lifetime:

| Time | RSS MB | Swap MB | Total MB |
|---|---:|---:|---:|
| Sun 19:00 | 251 | 314 | 565 |
| Mon 07:00 | 264 | 1,169 | 1,433 |
| Mon 19:00 | 427 | 2,061 | 2,488 |
| Tue 07:00 | 520 | 2,650 | 3,170 |
| Tue 19:00 | 658 | 3,195 | 3,853 |
| Wed 07:00 | 1,041 | 3,641 | 4,683 |
| Wed 13:00 | 1,114 | 3,926 | 5,040 |

That's a steady 60–90 MB/h, and it never goes down. A previous lifetime after a reboot went 281 MB → 2.4 GB in 33 h.

Some things I can add:

- **It grows with background runs, not with uploads.** Over those ~2.7 days the app was on screen for about 3 minutes and uploaded **one** photo. That fits your per-engine explanation: the hourly runs alone are enough to grow it.
- **Why lmkd never kills it:** the `MediaObserver` WorkManager job keeps waking the process, so it stays at oom_score_adj ~915–945. Every other cached app sits at 985–995 and gets killed first. When I woke the phone with swap full, lmkd killed 21 other apps in one minute (`low_swap_and_thrashing`), major faults hit ~6,600/s, and other apps hit ANRs and 8 s launches. The phone was fine again as soon as Immich's process was gone.
- **Killing the process isn't a workaround for instant backup.** After `am kill app.alextran.immich` (not force-stop; the jobs stay registered and the package isn't stopped), the next photo triggered `MediaObserver`. That started a new process, Android froze it about 10 s later, and **nothing was uploaded** until I opened the app. Normally uploads arrive within about a minute. So today the only options are a leaking process or delayed backups.

I'm now recording the engine index (`N.io`/`N.raster` thread names) next to memory, and will post whether it tracks the growth the same way yours does.
