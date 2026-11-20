# Basement guest room Modern Forms recovery

Live Node-RED tab: **Basement Guest Fan Recovery**, Admin API port 1880.

- Read live Home Assistant states every 30 seconds.
- Monitor `fan.basement_fan` and `light.basement_light`.
- Either entity `unavailable` or `unknown` continuously for two minutes, with Zooz supply `light.master_bath_fan_zooz_relay` ON, starts recovery. Ordinary fan/light OFF is healthy; an intentionally OFF supply is left alone.
- Turn the Zooz supply OFF, hold at least 30 seconds, then ON. Poll timing can extend the OFF interval to about 60 seconds.
- Allow three minutes after ON acknowledgement before another attempt.
- Maximum two attempts. On failure, send one Pushover alert to `global.BradPhoneNM` (verified as `pixel8`). Missing/invalid phone target suppresses sending rather than broadcasting.
- Keep attempts and lockout in persistent flow context. Both fan and light must stay healthy for ten minutes to reset the budget. Brief recovery does not reset it.
- Restore an outstanding OFF cycle on the next eligible tick after a deploy/restart. ON command errors get at most three restoration commands, one minute apart, then an alert. OFF command errors still lead to a restoration command. No queued service commands.
- A missing entity, stale HTTP response, or sampling gap pauses/restarts observation rather than triggering a new cycle based on stale data. No new OFF commands while a cycle is underway.

`controller.js` is the Function node source; `flow.json` is the reference flow. `build.py` generates the reference from installed node templates captured in `/tmp/nr-1880.json`. `test.js` exercises the real controller using simulated time and mocked node context; it does not control devices or send notifications.

Run tests: `node basement-fan-recovery/test.js` from the repository root.

For changes, read `../nodered-flow-agent-guide.md`, GET the tab using the ID in `deployed-id.txt`, back it up, update only that tab via PUT, re-read, and inspect container logs. Never edit deployed flow files directly.

Runtime inspection: `GET /context/flow/<tab-id>` exposes `recovery` and `lastSnapshot` through the local Admin API. Node-RED context uses its existing filesystem store and periodic disk flush; an abrupt host crash before a flush can lose the latest context update.

The user confirmed that `light.master_bath_fan_zooz_relay` is the actual guest room fan supply despite its misleading name. The initially selected `light.basement_guest_bed_switch` was incorrect; it was commanded back ON before deploying the corrected target, and the retry budget was reset. `previousWrongSupplyRecovery` preserves the initial attempt record.

Validation on 2026-09-14: scoped deploy re-read confirmed both service nodes target the corrected relay. Live snapshot showed fan OFF, light OFF (both available), and corrected relay ON; recovery idle with zero attempts. Controller simulation tests passed. No errors from the new flow appeared in deployment logs. The corrected relay was not deliberately cycled for a test because the user's manual toggle had already restored availability; failure-alert delivery was not live-tested.
