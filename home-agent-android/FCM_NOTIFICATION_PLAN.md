# Home Agent Android + FCM Notification Plan

## Summary

Implement Android session UX fixes, automatic reconnect, and reliable push notifications via Firebase Cloud Messaging. The app remains sideloaded/personal-use and LAN-controlled; only notification delivery uses Google FCM. The gateway does not need a public endpoint.

## Key Changes

- Keep session drawer grouping by `root_session_id`, render root conversations as collapsible groups, and collapse multi-session groups by default when the drawer opens.
- Replace the cramped reasoning row with a compact selector that keeps API/storage values as `low`, `medium`, `high`, and `xhigh`.
- Add Firebase Messaging support so the app can obtain an FCM registration token and register it with the Home Agent gateway.
- Add gateway device registration endpoints, a local push-token registry, FCM HTTP v1 sending, and session monitors for approval-needed, finished, and failed events.
- Centralize websocket reconnect behavior so the app reloads recent log state and reconnects automatically when a selected session is still running.

## Setup Requirements

- Create a Firebase project and add Android app package `com.homeagent.phone`.
- Place Firebase Android config at `/home/pi/cecret_lake/home-agent-android/google-services.json`.
- Create/download a Firebase service account JSON for the gateway host at `/home/pi/cecret_lake/home-agent/firebase-service-account.json`.
- Set gateway env vars: `HOME_AGENT_FCM_PROJECT_ID`, `HOME_AGENT_FCM_SERVICE_ACCOUNT_JSON`, and optionally `HOME_AGENT_PUSH_REGISTRY`.
- Keep Firebase config and service-account secrets out of git.

## Test Plan

- Drawer groups with many resumed sessions are collapsed by default and expandable.
- Reasoning choices render without ellipses on narrow phone screens.
- Starting a session, switching apps/locking phone, and reopening reconnects automatically when the session is still running.
- Registering an FCM token writes `push_tokens.json` through the gateway.
- `AWAITING_PHONE_APPROVAL:` triggers an FCM notification with the app closed.
- Session completion triggers a finished or failed notification with the app closed.
- Gateway restart resumes monitoring running sessions.

## Assumptions

- The phone has Google Play services and internet access.
- The gateway/Pi has outbound HTTPS access to Google FCM.
- The app remains sideloaded/personal-use; Play Store publication is not required.
- Notification delivery through Google is acceptable, while Home Agent control/data stays LAN-based.
