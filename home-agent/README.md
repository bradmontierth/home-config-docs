# Home Agent

LAN-only phone gateway for voice-triggered Codex sessions.

## Shape

- `gateway/`: phone web app, audio upload, `ffmpeg` normalization, Parakeet transcription, runner websocket proxy.
- `runner/`: host-side Codex PTY manager. It starts Codex from `/home/pi/home_config`, streams terminal output, accepts steering text, and stops the process group.
- `sessions/`: uploaded audio, transcripts, prompts, metadata, and Codex logs.

The gateway can run in Docker. The runner is intended to run directly on the host so Codex sees the same filesystem, SSH config, keys, and local tools it already uses.

## Session Flow

- `POST /api/sessions`: start a Codex run from transcript text.
- `GET /api/sessions`: list recent saved sessions for the phone session drawer.
- `GET /api/sessions/{session_id}/log`: return formatted terminal history from the saved `codex.log`.
- `POST /api/sessions/{session_id}/resume`: continue a saved Codex thread with new text.
- `WS /ws/sessions/{session_id}`: stream output and send live input while a run is active.

Resume requires a Codex thread id. New runs record it from the `thread.started` JSON event; older runs can still resume when the id is present in their saved `codex.log`.

## Local Development

Create the runner environment:

```bash
cd /home/pi/home_config/home-agent
python3 -m venv .venv-runner
.venv-runner/bin/pip install -r runner/requirements.txt
.venv-runner/bin/uvicorn runner.app:app --host 127.0.0.1 --port 8766
```

In another shell, create the gateway environment:

```bash
cd /home/pi/home_config/home-agent
python3 -m venv .venv-gateway
.venv-gateway/bin/pip install -r gateway/requirements.txt
.venv-gateway/bin/uvicorn gateway.app:app --host 0.0.0.0 --port 8767
```

Open:

```text
http://<this-host>:8767
```

For phone microphone access, use HTTPS for normal LAN access. A practical setup is Caddy, nginx, or uvicorn with a locally trusted certificate.

## Gateway In Docker

Start the host runner first, then:

```bash
cd /home/pi/home_config/home-agent
docker compose up -d --build
```

The compose file uses `network_mode: host` so the container can reach:

- `http://127.0.0.1:8766` for the runner
- `http://192.168.10.187:8090` for Parakeet

## Systemd

Without root, run the Codex runner as a user service and the gateway through Docker Compose:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/home-agent-runner.user.service ~/.config/systemd/user/home-agent-runner.service
systemctl --user daemon-reload
systemctl --user enable --now home-agent-runner
docker compose up -d --build
```

Check status:

```bash
systemctl --user status home-agent-runner
docker compose ps
curl http://127.0.0.1:8767/health
```

The user service remains available while the user's systemd manager is running. To make it survive reboots before login, enable lingering with `loginctl enable-linger pi`, which requires root.

For system-level services, install Python dependencies:

```bash
cd /home/pi/home_config/home-agent
python3 -m venv .venv-runner
.venv-runner/bin/pip install -r runner/requirements.txt
python3 -m venv .venv-gateway
.venv-gateway/bin/pip install -r gateway/requirements.txt
```

Install services:

```bash
sudo cp deploy/systemd/home-agent-runner.service /etc/systemd/system/
sudo cp deploy/systemd/home-agent-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now home-agent-runner home-agent-gateway
```

Check logs:

```bash
journalctl -u home-agent-runner -f
journalctl -u home-agent-gateway -f
```

## Security Notes

This should stay LAN-only. Set `HOME_AGENT_TOKEN` before routine use, and put the gateway behind HTTPS.

The runner defaults to:

```text
HOME_AGENT_CODEX_SANDBOX=workspace-write
HOME_AGENT_CODEX_APPROVALS=never
HOME_AGENT_CODEX_MODE=exec
HOME_AGENT_CODEX_DANGER_BYPASS=1
```

`HOME_AGENT_CODEX_MODE=exec` uses clean non-interactive JSON output for phone readability. `HOME_AGENT_CODEX_DANGER_BYPASS=1` launches Codex with `--dangerously-bypass-approvals-and-sandbox`; this is intentionally broad and should stay LAN-only until a safer app toggle exists. The older interactive TUI mode can be enabled with `HOME_AGENT_CODEX_MODE=interactive`, but Android needs a real terminal renderer for that output.

By default, successful command output is collapsed in the phone stream so file reads, searches, API JSON, and log scans do not overwhelm the terminal. Raw JSONL still remains in each session `codex.log`. Set `HOME_AGENT_SHOW_COMMAND_OUTPUT=1` on the runner only when you want successful command stdout streamed to the phone.

## Codex Account Switching

The runner supports multiple cached Codex auth stores by setting `CODEX_HOME`
per session. The Android settings screen lists the configured accounts and sends
the selected account id with new and resumed sessions.

Default account slots:

```text
/home/pi/cecret_lake/home-agent/codex-accounts/account1
/home/pi/cecret_lake/home-agent/codex-accounts/account2
/home/pi/cecret_lake/home-agent/codex-accounts/account3
machine -> the normal machine login, usually /home/pi/.codex
```

Login each account once from SSH. Do not move token files between accounts.
Run the same commands again whenever a slot expires.

```bash
mkdir -p /home/pi/cecret_lake/home-agent/codex-accounts/account1
CODEX_HOME=/home/pi/cecret_lake/home-agent/codex-accounts/account1 codex login --device-auth

mkdir -p /home/pi/cecret_lake/home-agent/codex-accounts/account2
CODEX_HOME=/home/pi/cecret_lake/home-agent/codex-accounts/account2 codex login --device-auth

mkdir -p /home/pi/cecret_lake/home-agent/codex-accounts/account3
CODEX_HOME=/home/pi/cecret_lake/home-agent/codex-accounts/account3 codex login --device-auth

codex login --device-auth
```

Optional runner environment:

```text
HOME_AGENT_CODEX_ACCOUNTS_ROOT=/home/pi/cecret_lake/home-agent/codex-accounts
HOME_AGENT_CODEX_ACCOUNTS=account1:Account 1,account2:Account 2,account3:Account 3,machine:Machine Login
HOME_AGENT_CODEX_DEFAULT_ACCOUNT=account1
HOME_AGENT_CODEX_ACCOUNT_LABELS=/home/pi/cecret_lake/home-agent/codex-account-labels.json
```

The `machine` account id is special: the runner does not set `CODEX_HOME` for
that option, so Codex falls back to the login for the `pi` user running the
service. To make it the default option, set `HOME_AGENT_CODEX_DEFAULT_ACCOUNT=machine`.

The labels shown on Android can be renamed from Settings. They are stored as
metadata in `codex-account-labels.json`; the app does not inspect or parse
Codex token files. `codex login status` currently reports only whether a slot is
logged in, so account identity must be labeled manually.

Only use danger bypass when the host account and network boundary are intentionally constrained.

## Parakeet

The gateway converts browser audio to:

```text
16 kHz mono PCM WAV
```

Then posts it to:

```text
http://192.168.10.187:8090/parakeet/transcribe?chunk_seconds=300&context_seconds=2
```

Configure a different endpoint with:

```text
HOME_AGENT_PARAKEET_URL=http://192.168.10.187:8090
```
