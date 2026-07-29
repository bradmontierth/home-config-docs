# GX10 prod stack — rename + staged restart (cutover steps)

Written 2026-07-28, **not applied**. Two containers serve production but are
named and configured like throwaways:

| now | becomes | what it actually is |
| --- | --- | --- |
| `music-llm-qwen36-aeon-dflash-test` | `local-llm` | the house LLM — every voice command's intent parse goes through it |
| `kokoro-gb10-bench` | `kokoro-tts` | the voice assistant's TTS fallback (`:8880`) |

Both currently have `restart=no` and no compose management, so **a power cut
leaves the kitchen assistant unable to parse any command until someone SSHes in
and starts them by hand**. The proxy on `:8095` *does* come back (user unit,
`Linger=yes`) and will cheerfully forward to a dead port. The rename is
cosmetic; this is the part that matters.

## What must NOT change

`--served-model-name qwen3-next` in the compose file, and `--served-model
qwen3-next` in `music-llm-openai-proxy.service`. The orchestrator sends
`{"model": "qwen3-next"}` from `orchestrator/config.py: LLM_MODEL`. Those three
move together or not at all. The *container* name is free.

## Cost

~2-5 minutes with no LLM: recreating the vllm container means a cold weight
load. Every voice command fails during that window. Do it when nobody's using
the kitchen, and not while the house is on battery.

## Steps

```bash
# 1. stage the definitions (safe any time — inert until compose runs)
ssh dgx 'mkdir -p /home/pi/local-llm /home/pi/kokoro-tts'
scp gx10-prod-stack/local-llm.docker-compose.yml  dgx:/home/pi/local-llm/docker-compose.yml
scp gx10-prod-stack/kokoro-tts.docker-compose.yml dgx:/home/pi/kokoro-tts/docker-compose.yml
scp gx10-prod-stack/start-prod-stack.sh           dgx:/home/pi/local-llm/
ssh dgx 'chmod +x /home/pi/local-llm/start-prod-stack.sh'

# 2. disarm the superseded kokoro definition FIRST.
#    It pins container_name: kokoro-gb10-bench on the same :8880 — leave it and
#    a later `compose up` in that dir starts a rival container.
ssh dgx 'mv /home/pi/kokoro-gb10-benchmark/docker-compose.yml \
            /home/pi/kokoro-gb10-benchmark/docker-compose.yml.superseded'

# 3. cutover (the ~2-5 min outage starts here)
ssh dgx 'docker rm -f music-llm-qwen36-aeon-dflash-test kokoro-gb10-bench'
ssh dgx '/home/pi/local-llm/start-prod-stack.sh'      # staged bring-up, logs as it goes

# 4. verify BEFORE walking away
ssh dgx 'curl -s localhost:8102/health; curl -s localhost:8880/health'
curl -s -X POST http://127.0.0.1:8785/command -H 'Content-Type: application/json' \
     -d '{"text":"set a test timer for 10 seconds"}'   # must return intent=set_timer
ssh dgx 'curl -s -m 60 -X POST localhost:8093/synthesize -H "Content-Type: application/json" \
     -d "{\"text\":\"cutover check\",\"voice\":\"gandalf_calm\"}" -o /tmp/t.wav -w "%{http_code}\n"'

# 5. make it survive reboots
scp gx10-prod-stack/gx10-prod-stack.service dgx:~/.config/systemd/user/
scp gx10-prod-stack/gx10-prod-stack.timer   dgx:~/.config/systemd/user/   # optional watchdog
ssh dgx 'export XDG_RUNTIME_DIR=/run/user/$(id -u)
         systemctl --user daemon-reload
         systemctl --user enable gx10-prod-stack.service
         systemctl --user enable --now gx10-prod-stack.timer'   # optional
```

## Rollback

The old containers are gone after step 3, but nothing else was touched — the
images, the model dirs under `/home/pi/models`, and the proxy unit are
untouched. To go back, `docker rm -f local-llm kokoro-tts`, restore
`docker-compose.yml.superseded`, and re-run whatever originally started them
(the vllm run command is preserved verbatim in `local-llm.docker-compose.yml`).

## Follow-ups not done here

- `/home/pi/music-llm` (the directory), `music-llm-openai-proxy.service`, and
  `home_config/beelink-agents-gx10-music-llm.md` still carry the "music" name.
  The unit's `ExecStart` and `WorkingDirectory` point into that directory, so
  renaming it means editing the unit too. Worth doing, but it's a separate
  change with its own outage on `:8095`.
- `voice-assistant-plan.md:250` documents `docker start
  music-llm-qwen36-aeon-dflash-test` as the manual recovery step. Update it
  after cutover — or delete it, since the point of this change is that no
  manual step should be needed.
- `gx10-echo-audio-gallery` (`:8094`) is also hand-run with `restart=no`, but
  nothing references it in home_config. Left alone pending a decision on
  whether it's still wanted.
