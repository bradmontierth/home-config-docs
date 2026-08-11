# Whole-Home Amp Lease Manager Plan

Date: 2026-08-10

Status: Implemented through Phase 5; ten-minute automatic shutdown is enabled

## Deployment Record

Implemented on 2026-08-10:

- Standalone `amp-lease-manager` container is healthy on host port `8462`.
- The service validates R3 unique ID `3845559058.28-37-3-currentValue` at startup and periodically.
- Persistent state and restart reconciliation are active.
- The five exact Music Assistant Snapcast players are monitored every five seconds.
- `home-audio-adapter` acquires before play/resume, renews through a non-waking endpoint, and releases after pause/stop.
- Node-RED's ten shared `Amp Speakers` instances use the manager.
- Voice stage-two prewake uses the manager; its obsolete audio wake tone was removed.
- Node-RED's manager-failure branch uses a fixed R3 Home Assistant turn-on and five-second delay. It never sends an off command.
- Manager and Node-RED API credentials are stored outside flow function source.
- `ALLOW_AUTOMATIC_OFF=true` is deployed with the production 600-second idle hold.

Validation results:

- Unit tests: manager `11 passed`; adapter `6 passed`.
- A 4.0-second candidate returned ready as designed but lost `zero` in one of three recorded cold audio trials, proving the original margin was insufficient.
- A 5.0-second candidate passed three of three recorded cold audio trials. The master-closet microphone transcripts all began `Zero, one, two...`; readiness requests took 5.064 s, 5.079 s, and 5.083 s.
- A follow-up 4.25-second candidate was rejected on its first cold audio trial: readiness returned in 4.327 s, but the recorded opening was `So one, two...` rather than an intact `Zero, one, two...`. Production remains at 5.0 seconds.
- A follow-up 4.5-second candidate was also rejected on its first cold audio trial: readiness returned in 4.592 s, but the recorded opening was `One, two, three, four, five`, with `zero` absent. Production remains at 5.0 seconds.
- A cold Node-RED broadcast produced the complete closet-mic transcript `This is the AMP Lease Manager end-to-end test`.
- A cold adapter play produced `Zero, one, two, three, four, five`, then stop released the named lease.
- Manager restart recovered state and hold without sending another relay command.
- The live fixed-R3 Node-RED fallback completed its Home Assistant call and delay while R1/R2 remained off.
- Automatic shutdown was validated first with a temporary 30-second hold: with healthy HA/MA state, no leases, and no active players, the manager issued the R3-only `switch.turn_off` command. R1/R2 remained off and unchanged. The production hold was then restored to 600 seconds while R3 was off.

Phase 5 is active. Every successful touch/acquire/renew/release extends or preserves the shared hold, active Music Assistant players inhibit shutdown, and dependency uncertainty inhibits shutdown. Normal household activity now exercises the complete cold on → active → ten-minute hold → automatic off lifecycle.

## Decision

Build the amp lease manager as a standalone service, not as Home Assistant automation logic and not inside `home-audio-adapter`.

The service will own the policy for the whole-home amp trigger. Home Assistant will remain the hardware control plane for Z-Wave and the ZEN16. Node-RED and `home-audio-adapter` will become clients of the lease manager. Tempo and AntennaPod will continue to use `home-audio-adapter`; they will not need their own relay logic.

The lease manager may be deployed alongside `home-audio-adapter`, but it must run as a separate container/process with its own health, persistence, logs, restart policy, and release lifecycle.

## Why This Boundary

- The relay is shared infrastructure used by announcements, voice satellites, and interactive playback. Its lifetime cannot safely belong to any one consumer.
- A standalone manager can serialize concurrent requests and maintain a single ten-minute hold across all consumers.
- Restarting or deploying `home-audio-adapter` will not discard relay ownership or interrupt Node-RED announcements.
- Restarting Home Assistant will temporarily remove the Z-Wave control path, but it will not erase the manager's leases. The manager can reconnect and reconcile when Home Assistant returns.
- Consumers do not receive the Home Assistant token and cannot accidentally address either 120 V fan relay.
- This follows the house convention that orchestration lives in a standalone application or Node-RED rather than Home Assistant scripts.

## Confirmed Hardware Mapping

The physical mapping and entity identities are now:

| ZEN16 endpoint | Electrical role | Home Assistant entity | Immutable Z-Wave unique ID |
| --- | --- | --- | --- |
| R1 | Whole-house fan low, 120 V control | `switch.whole_house_fan_relay_low` | `3845559058.28-37-1-currentValue` |
| R2 | Whole-house fan high, 120 V control | `switch.whole_house_fan_relay_high` | `3845559058.28-37-2-currentValue` |
| R3 | Whole-home amp trigger, 5 V | `switch.whole_home_audio_amp_trigger` | `3845559058.28-37-3-currentValue` |

The ZEN16 DC motor mode remains enabled for R1 and R2, with R3 unaffected.

The lease manager must have the R3 entity and expected unique ID as fixed configuration. It must validate the entity-registry mapping at startup and refuse all relay writes if the configured entity no longer resolves to the expected R3 unique ID. It must never accept a caller-supplied Home Assistant entity ID. Its only permitted Home Assistant writes are `switch.turn_on` and `switch.turn_off` for `switch.whole_home_audio_amp_trigger`.

## Measured Timing Baseline

The calibrated `0` through `20` spoken-number test was run three times from a cold amp using direct Music Assistant announcement playback with pre-announcement disabled. The first audible word was `three` in all three runs. A warm control began at `zero`.

This establishes:

- There is no hidden Music Assistant or Snapcast ding/padding affecting the numeric counter.
- The relay/Home Assistant acknowledgement is fast, approximately 44–59 ms in the observed tests.
- Cold amp readiness is approximately 3.8–3.9 seconds after the relay request.
- A 4.0-second candidate lost the opening `zero` in one of three cold acoustic validation runs.
- A 5.0-second candidate passed three of three cold acoustic runs; each closet-mic transcript began `Zero, one, two...`.
- A 4.25-second follow-up failed its first cold acoustic run by damaging the opening `zero` to `So`; it was rejected without further trials.
- A 4.5-second follow-up failed its first cold acoustic run by losing the opening `zero` entirely; it was rejected without further trials.
- The production readiness delay is therefore 5.0 seconds after a confirmed off-to-on transition.
- An already-on amp should not incur this delay.

The existing WAV remains the acceptance-test asset:

`/home/pi/homeassistant/config/www/amp_wake_test/amp_wake_timing_0_to_20.wav`

## Goals

1. Turn the amp on before any consumer begins audible playback.
2. Keep it on while any relevant playback is active.
3. Hold it on for ten minutes after the most recent relevant activity.
4. Make repeat speech and playback within the hold window immediate.
5. Prevent one consumer from turning the amp off while another still needs it.
6. Reconcile safely across manager, Home Assistant, Music Assistant, Node-RED, and adapter restarts.
7. Default to leaving the amp on whenever dependency state is uncertain.
8. Produce enough status and event history to explain every relay transition.

## Non-Goals

- The manager will not control the whole-house fan.
- It will not replace Home Assistant as the Z-Wave controller.
- It will not replace Music Assistant or Snapcast playback control.
- It will not make Tempo or AntennaPod aware of Home Assistant.
- It will not initially optimize power usage more aggressively than a ten-minute idle hold.
- It will not infer that `pause`, volume, seek, or status operations require a cold wake.

## Proposed Deployment

Create a small service at:

`/home/pi/amp-lease-manager`

Deploy it as a container named `amp-lease-manager` with:

- Host port `8462` mapped to the service port. Port 8462 was unused when this plan was prepared.
- `restart: unless-stopped`.
- A persistent `/data` volume for state.
- Home Assistant at `http://192.168.10.217:8123`.
- Music Assistant at `http://192.168.10.217:8095`.
- An API bearer token distinct from the Home Assistant token.
- Secrets supplied from a permission-restricted environment file under the secret lake, for example `/home/pi/cecret_lake/amp-lease-manager/amp-lease-manager.env`.

The service should bind to the host LAN interface because Node-RED and the adapter run in containers and must be able to reach it. Access should be restricted to the trusted LAN/host. Only the manager receives the Home Assistant credential; consumer requests use the manager's narrower bearer token.

Expected repository contents:

```text
/home/pi/amp-lease-manager/
  app/
    api.py
    config.py
    ha_client.py
    lease_manager.py
    ma_monitor.py
    main.py
    models.py
    persistence.py
  tests/
  Dockerfile
  docker-compose.yml
  pyproject.toml
  README.md
```

## Ownership Model

The manager combines two signals:

1. Explicit leases from clients.
2. Observed playback activity from the five whole-home Music Assistant/Snapcast players.

The relay must be on if either signal says it is needed. It may be turned off only when all of the following are true:

- No explicit lease is active.
- No monitored player is playing or running an announcement.
- The ten-minute hold has expired.
- Home Assistant state is currently available.
- Music Assistant state is currently available and has completed at least one successful reconciliation since service startup.
- The configured entity-to-R3 safety validation is passing.

This is deliberately biased toward an unnecessary period of amp-on time instead of a missed announcement.

## Lease Types

### Activity touch

An activity touch represents a short event such as a text-to-speech announcement or voice response. It immediately extends the global hold deadline to at least ten minutes from now. The request waits for amp readiness before returning.

Touches do not require a matching release. They expire at the global hold deadline.

### Named session lease

A named session lease represents interactive playback such as a Tempo or AntennaPod session. It has a stable owner/key and a renewable expiry so a crashed client cannot hold the amp forever.

The adapter renews the lease while its playback session is active. Pause, stop, playback completion, or replacement releases the named lease, but release still leaves the normal ten-minute global hold.

### Music Assistant observed lease

The manager polls Music Assistant and treats any monitored amp player in an active playback or announcement state as an internal lease. This is the safety backstop for client crashes, incomplete event delivery, direct Music Assistant use, and consumers not yet migrated.

An observed lease cannot be released through the API. It clears only when Music Assistant reports the monitored players inactive.

## Consumer Semantics

| Consumer action | Lease-manager behavior | Playback behavior |
| --- | --- | --- |
| Start announcement | Touch and wait for ready | Start only after success |
| Voice prewake | Touch and wait for ready | Wake pipeline continues after success |
| Play/resume media | Acquire/renew named lease and wait for ready | Send Music Assistant play/resume after success |
| Pause media | Release named lease into ten-minute hold | Send pause; never issue a turn-on just for pause |
| Stop/completion | Release named lease into ten-minute hold | Stop normally |
| Seek/volume/queue/status | No new lease solely for this command | Operate normally |
| Repeated request during hold | Extend hold; return ready immediately if relay remains on | No cold-start delay |

This implements the requested debounce/hold/status gate: every play path asks the manager, but the manager checks actual and desired state and only performs a physical turn-on when needed.

## Readiness Algorithm

All acquire/touch operations are serialized around relay state transitions.

1. Record or renew the lease and persist its deadline before interacting with dependencies.
2. Read the current Home Assistant state for the exact R3 entity.
3. If the switch is already `on` and the latest confirmed on transition is at least 5.0 seconds old, return ready immediately.
4. If it is `on` but still inside the 5.0-second readiness window, wait only for the remainder.
5. If it is `off`, request `switch.turn_on` for the exact R3 entity.
6. Wait for Home Assistant to confirm `on` and start a fresh 5.0-second readiness window from that confirmed transition.
7. Return success only after the readiness deadline.
8. Concurrent callers join the same transition and readiness wait instead of sending duplicate commands.

The caller should not implement its own fixed delay. This keeps the measured readiness policy in one place and allows it to be tuned without changing every consumer.

## State Model

The externally useful state can be represented as:

| State | Meaning |
| --- | --- |
| `off` | Relay confirmed off and nothing requires it |
| `waking` | Relay turn-on requested or confirmed, but the 5.0-second window is incomplete |
| `ready` | Relay confirmed on and ready for audio |
| `holding` | No active playback, but the ten-minute deadline has not expired |
| `active` | One or more explicit or observed leases are active |
| `degraded` | A dependency or safety validation is unavailable; automatic off is inhibited |
| `inhibited` | Optional maintenance state in which automated relay writes are disabled |

`active` and `holding` describe policy; the API response should also expose the independently observed relay state so diagnostics do not confuse desired state with physical state.

## API Contract

### `POST /v1/acquire`

Acquire or renew a named lease and, by default, wait until the amp is ready.

Example request:

```json
{
  "owner": "home-audio-adapter",
  "lease_id": "playback-session-123",
  "reason": "antennapod play",
  "ttl_seconds": 120,
  "wait_for_ready": true
}
```

### `POST /v1/touch`

Record one-shot activity, extend the hold, and wait until ready.

Example request:

```json
{
  "owner": "node-red",
  "reason": "water leak announcement",
  "wait_for_ready": true
}
```

### `POST /v1/release`

Release a named session. A release does not turn the relay off immediately; it starts or preserves the ten-minute hold.

### `POST /v1/renew`

Renew an active named lease without issuing a relay-on command. The adapter uses this for status-confirmed active playback so status polling itself is not a cold-wake operation.

### `GET /v1/status`

Return:

- Policy state and observed relay state.
- `ready` and `ready_at`.
- Global `hold_until`.
- Active named leases, owners, reasons, and expiries.
- Current Music Assistant observed activity.
- Home Assistant/Music Assistant connection freshness.
- Entity safety-validation result.
- Last on/off command and observed transition.
- Whether automatic off is currently allowed.

### Health endpoints

- `GET /healthz`: process liveness only.
- `GET /readyz`: configuration loaded, persistent state available, R3 mapping validated, and initial dependency reconciliation completed.

Mutating endpoints require the manager API bearer token. Status and health may be limited to the trusted network, or status may use the same token.

## Persistence and Restart Recovery

Persist at least:

- Global hold deadline as an absolute UTC timestamp.
- Named leases and their absolute expiries.
- Last confirmed relay state and timestamp.
- Last on/off command timestamps and reasons.
- Service state schema version.

Write state atomically using a temporary file, `fsync`, and replace. Use monotonic time for waits within a running process and wall-clock UTC timestamps for recovery across restarts.

On manager startup:

1. Load persisted state and discard expired leases.
2. Validate that the configured Home Assistant entity still maps to the expected R3 unique ID.
3. Read actual relay state.
4. Read all monitored Music Assistant player states.
5. If the relay is on with no recovered need, begin a fresh ten-minute recovery hold rather than immediately switching it off.
6. If an unexpired lease requires the relay and it is off, turn it on and restart the readiness clock.
7. Permit automatic off only after both dependency reconciliations have succeeded.

On Home Assistant restart or outage:

- Preserve and continue extending leases.
- Do not send an off request based on stale state.
- Retry connection and revalidate R3 before resuming writes.
- Do not claim a cold amp is ready when its current physical state cannot be confirmed.
- Return a clear `503 relay_state_unknown` for a readiness request when confirmation is impossible.

On Music Assistant restart or outage:

- Explicit acquire/touch requests may still turn on the relay if Home Assistant is healthy.
- Automatic off is inhibited while monitored playback state is unknown.
- Resume normal expiry behavior only after a successful full-player reconciliation.

On unexpected external relay-off while a valid lease or active player exists:

- Treat it as a fault.
- Reassert `on` for R3.
- Start a new 5.0-second readiness window.
- Emit a prominent structured log event.

## Music Assistant Backstop

Poll `players/all` on a short interval, initially five seconds, and monitor the exact five amp/Snapcast players used by whole-home audio. Store their stable player IDs in configuration rather than matching display-name substrings.

Consider a player active when Music Assistant reports active playback or an announcement in progress. The implementation must be tested against the actual Music Assistant payloads to define the exact state fields.

While any monitored player is active:

- Hold desired relay state on.
- Renew the internal observed lease.
- Never allow the expiry worker to send off.

When all are inactive, the normal ten-minute hold applies.

## Node-RED Integration

The existing shared `Amp Speakers` subflow is used by ten instances across announcements and voice-related flows. It is the primary integration point.

Change the subflow so its pre-play path:

1. Calls `POST /v1/touch` with a useful `owner` and `reason`.
2. Waits for a successful ready response.
3. Sends the real announcement without the current wake chime or client-side fixed delay.
4. Records a visible Node-RED error and follows the electrical fallback policy if the manager cannot confirm readiness.

The Voice Broadcast stage-two prewake path should call the same endpoint. A later shared-subflow call for the actual response may safely touch again; it will simply extend the hold and return immediately.

Node-RED changes must follow `nodered-flow-agent-guide.md`:

- Use the running Admin API as source of truth.
- Because the target is a shared subflow definition rather than an ordinary tab, fetch the complete flow set, make a narrowly scoped change, and deploy through the Admin API using the appropriate full-flow endpoint.
- Do not edit the deployed `flows.json` directly.
- Preserve credentials and revision metadata.
- Re-read the deployed flow, allow Home Assistant nodes to reconnect, and check logs after deployment.

During rollout, migrate a noncritical test path first. After validation, one shared-subflow update will cover its ten current instances. The old heuristic global such as `wholeHomeAmpLikelyOn` should be removed only after the manager is proven in production.

Because the amp is now in trigger mode, an audio chime cannot wake it when R3 is off. The Node-RED failure branch must therefore use the existing Home Assistant connection to call `switch.turn_on` for the fixed `switch.whole_home_audio_amp_trigger` entity, wait five seconds, and then continue. It must never target a caller-supplied entity and must never target R1 or R2.

## `home-audio-adapter` Integration

Add an `AmpLeaseClient` to `/home/pi/home-audio-adapter` and configure:

```text
AMP_LEASE_BASE_URL=http://192.168.10.217:8462
AMP_LEASE_TOKEN=<manager-specific token>
AMP_LEASE_REQUEST_TIMEOUT_SECONDS=8
```

The adapter should:

- Acquire a deterministic named lease before `play` and `resume` and await readiness before forwarding the Music Assistant command.
- Renew the named lease while playback is active.
- Release it after successful pause, stop, completion, or session replacement.
- Not acquire merely for pause, status, volume, seek, queue inspection, or other control-only calls.
- Surface a clear error when a new cold playback cannot obtain confirmed readiness.
- Use bounded retries and idempotent lease IDs so an HTTP retry does not create extra leases.

Tempo and AntennaPod already send their remote playback through the adapter on port 8461. They should remain unchanged unless testing reveals a playback route that bypasses the adapter.

## Failure Policy for Critical Announcements

The amp is operating in trigger mode. Unlike auto-signal mode, sending an audio chime to a sleeping amp cannot turn it on. The previous audio wake/chime is therefore not a valid fallback once R3 is off.

For the initial rollout, Node-RED critical announcements must use this failure branch when the lease manager returns an error or times out:

1. Call Home Assistant `switch.turn_on` for the fixed `switch.whole_home_audio_amp_trigger` entity.
2. Wait the full five-second cold-readiness interval.
3. Continue to the real announcement.
4. Emit a visible warning identifying that the manager was bypassed.

This duplicates the minimum wake behavior only for failure containment. The fallback must never send `switch.turn_off`; shutdown remains exclusively owned by the manager. It must never accept an entity ID from the incoming message.

Water-leak and other critical alerts should retain the electrical fallback through the soak period. `home-audio-adapter` will not receive Home Assistant credentials; if it cannot reach the manager for a new cold playback, it should return a controlled error rather than start inaudible playback.

Until this electrical fallback is deployed and tested, keep R3 on continuously and keep automatic off disabled. Automatic off must not be enabled until all critical consumers have migrated and the fallback has been validated.

## Observability

Emit structured events for:

- Lease acquire, renew, expiry, and release.
- Hold deadline extension.
- Relay state observations and commands.
- Readiness waits and their duration.
- Music Assistant observed activity changes.
- Home Assistant/Music Assistant disconnect and recovery.
- Safety validation failures.
- Suppressed off attempts and their reason.
- Unexpected external relay transitions.

Never log credentials. Include owner, lease ID, reason, request correlation ID, and state transition where applicable.

`GET /v1/status` should make it possible to answer, without log archaeology, why the amp is currently on and when it may turn off.

## Implementation Phases

### Phase 0: Build and unit-test the standalone manager

- Scaffold the service, API, clients, persistence, and state machine.
- Add fake Home Assistant and Music Assistant clients.
- Unit-test concurrent acquire, repeated touch, delayed confirmation, lease renewal, expiry, persistent restart, clock changes, and dependency outages.
- Test the R3 unique-ID safety interlock and prove it rejects R1/R2 or a remapped entity.

### Phase 1: Read-only and shadow operation

- Deploy the manager with relay writes and automatic off disabled.
- Keep R3 continuously on until the critical Node-RED electrical fallback is deployed and tested; an audio chime cannot wake an amp in trigger mode.
- Validate R3 mapping, observe real Home Assistant state, and observe the five Music Assistant players.
- Compare its desired state and proposed transitions to actual household use.
- Confirm container restart and state recovery without affecting audio.

### Phase 2: Enable turn-on only

- Allow the manager to turn on R3 but prohibit automatic off.
- Run three cold calibrated-number tests through the manager.
- Verify real content starts only after readiness and begins at `zero` in the calibrated test.
- Verify repeat acquisition while on returns without a five-second delay.
- Verify simultaneous callers result in one relay transition.

### Phase 3: Migrate Node-RED

- Create a single noncritical test path first.
- Migrate voice prewake and the shared `Amp Speakers` subflow through the Node-RED Admin API.
- Replace the obsolete audio-chime failure fallback with the fixed R3 Home Assistant turn-on plus five-second gate.
- Validate that the fallback can wake the amp before allowing any automatic shutoff.
- Re-read deployed flows and check Node-RED logs and Home Assistant node reconnection.
- Confirm the ten-minute hold covers conversational follow-up without another cold wake.

### Phase 4: Migrate `home-audio-adapter`

- Add lease calls to play/resume and release calls to pause/stop/completion.
- Test Tempo and AntennaPod independently.
- Confirm pressing pause while the relay is off does not turn it on.
- Confirm resume during the hold is immediate.
- Confirm direct or crashed-client playback is caught by Music Assistant polling.

### Phase 5: Enable automatic off

- Enable expiry-driven off only after all known consumers are covered.
- Start with the ten-minute hold.
- Observe at least several normal announcement, voice, Tempo, and AntennaPod cycles.
- Prove the manager never sends off while any explicit lease or monitored player is active.
- Exercise manager restart, Home Assistant unavailability, and Music Assistant unavailability; each uncertainty must inhibit automatic off.

### Phase 6: Cleanup

- Remove the old success-path wake chime and fixed three-second delays.
- Remove `wholeHomeAmpLikelyOn` and other duplicated amp-state heuristics.
- Retain or remove the critical-alert fallback based on the explicit post-soak decision.
- Document operations, troubleshooting, manual inhibit, backup, and recovery.

## Validation Matrix

| Scenario | Expected result |
| --- | --- |
| Cold announcement | One R3 on command; content begins after confirmed 5.0-second readiness |
| Warm announcement within hold | No relay command and no readiness delay; hold extends ten minutes |
| Voice follow-up | Immediate response path while the original hold remains active |
| AntennaPod/Tempo play | Named lease acquired before Music Assistant play |
| Pause while amp is off | No turn-on command |
| Pause after playback | Lease released; amp remains on for ten minutes |
| Two simultaneous announcements | One wake transition; both callers share readiness result |
| Announcement during media playback | Existing media/observed lease prevents off; hold extends |
| Manager restart while on | Persisted state recovered; no immediate off |
| Home Assistant restart | No off; leases retained; writes resume only after R3 revalidation |
| Music Assistant restart | No automatic off until player reconciliation succeeds |
| Adapter crash during playback | Music Assistant observed lease keeps relay on |
| Node-RED manager timeout | Fixed R3 electrical turn-on, five-second gate, visible warning, then the critical announcement |
| Entity accidentally remapped | Safety validation blocks writes and reports degraded state |
| External off during active lease | Manager reasserts R3 on and restarts readiness timer |

## Acceptance Criteria

The migration is complete when:

- Three cold calibrated tests begin at `zero` when playback is launched after manager readiness.
- Warm acquire/touch requests return promptly without a redundant five-second delay.
- There are no relay-off commands during active or announced playback in validation logs.
- The ten-minute hold is extended by repeat voice, announcement, or media activity.
- Tempo and AntennaPod do not turn on the amp for pause/status-only operations.
- Node-RED's ten shared-subflow consumers use the manager on their success path.
- Critical Node-RED paths use a tested fixed-R3 electrical fallback, not an audio chime.
- Manager, Home Assistant, Music Assistant, Node-RED, and adapter restart tests all behave according to the failure policy.
- The service refuses relay writes when R3 identity validation fails.
- The old heuristic and wake chime are no longer required on a successful request path.
- A documented rollback can leave the amp safely on and restore the previous audio wake behavior.

## Rollback

Rollback must favor audibility over power savings:

1. Disable the manager's automatic-off capability.
2. Manually leave `switch.whole_home_audio_amp_trigger` on if relay control is uncertain.
3. Restore or retain the Node-RED fixed-R3 electrical wake plus five-second delay through the Admin API; do not rely on an audio wake in trigger mode.
4. Disable adapter lease enforcement or deploy the prior adapter image.
5. Leave the corrected R1/R2/R3 entity names and physical mapping in place; they are independent of this software rollout and should not be reversed.

## Deployed Production Configuration

```text
AMP_RELAY_ENTITY_ID=switch.whole_home_audio_amp_trigger
AMP_RELAY_EXPECTED_UNIQUE_ID=3845559058.28-37-3-currentValue
AMP_COLD_READY_SECONDS=5.0
AMP_IDLE_HOLD_SECONDS=600
AMP_NAMED_LEASE_TTL_SECONDS=120
MA_POLL_SECONDS=5
ALLOW_TURN_ON=true
ALLOW_AUTOMATIC_OFF=true
```

`ALLOW_AUTOMATIC_OFF` remained false through the turn-on-only, Node-RED, and adapter migration phases. It was enabled as a separate observable rollout step after those paths and the fixed-R3 fallback passed validation. A temporary 30-second hold proved the live off transition before the deployed value was restored to 600 seconds.
