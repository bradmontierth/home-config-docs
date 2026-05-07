# Home Agent

LAN-only phone gateway for voice-triggered Codex sessions.

## Shape

- `gateway/`: phone web app, audio upload, `ffmpeg` normalization, Parakeet transcription, runner websocket proxy.
- `runner/`: host-side Codex PTY manager. It starts Codex from `/home/pi/home_config`, streams terminal output, accepts steering text, and stops the process group.
- `sessions/`: uploaded audio, transcripts, prompts, metadata, and Codex logs.

The gateway can run in Docker. The runner is intended to run directly on the host so Codex sees the same filesystem, SSH config, keys, and local tools it already uses.

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
- `http://192.168.10.197:8090` for Parakeet

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

Only use danger bypass when the host account and network boundary are intentionally constrained.

## Parakeet

The gateway converts browser audio to:

```text
16 kHz mono PCM WAV
```

Then posts it to:

```text
http://jetson-tts:8090/parakeet/transcribe?chunk_seconds=120&context_seconds=2
```

Configure a different endpoint with:

```text
HOME_AGENT_PARAKEET_URL=http://192.168.10.197:8090
```
