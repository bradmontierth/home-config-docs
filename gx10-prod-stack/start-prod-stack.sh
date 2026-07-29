#!/usr/bin/env bash
# Staged startup for the GX10's GPU production containers.
# Deploy to /home/pi/local-llm/start-prod-stack.sh (run by gx10-prod-stack.service).
#
# Why staged rather than `restart: unless-stopped` on each container: on this
# box every GPU allocation is unified memory, invisible to the kernel OOM
# killer (2026-07-05: an uncapped job OOMed the host, killed vllm, livelocked
# ssh, needed a power cycle). Docker starting the ~54GB vllm at daemon boot at
# the same instant the parakeet workers + sortformer + speaker-embed load their
# models is exactly that race. So: wait for the ASR stack to answer, bring up
# the LLM, wait for it to be healthy, THEN kokoro.
#
# Idempotent by design — `compose up -d` on a healthy container is a no-op, so
# this doubles as a watchdog when driven by gx10-prod-stack.timer.
set -uo pipefail

LLM_DIR=/home/pi/local-llm
TTS_DIR=/home/pi/kokoro-tts
log() { echo "[$(date -Is)] $*"; }

wait_for() {  # wait_for <label> <url> <timeout_s>
    local label=$1 url=$2 timeout=$3 waited=0
    while [ "$waited" -lt "$timeout" ]; do
        if curl -fsS -m 5 -o /dev/null "$url" 2>/dev/null; then
            log "$label ready after ${waited}s"
            return 0
        fi
        sleep 5
        waited=$((waited + 5))
    done
    log "WARN: $label not ready after ${timeout}s; continuing anyway"
    return 1
}

log "waiting for docker daemon"
for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 5; done
docker info >/dev/null 2>&1 || { log "FATAL: docker never came up"; exit 1; }

# The ASR stack self-starts (restart: unless-stopped). Let it finish loading
# its models before we ask the GPU for another ~54GB. /jobs is used rather than
# /health because the parakeet API has no /health route.
wait_for "parakeet ASR (:8090)" "http://127.0.0.1:8090/jobs" 300

log "starting local-llm"
docker compose -f "$LLM_DIR/docker-compose.yml" up -d
# vllm loads weights + compiles graphs; cold start is minutes, not seconds.
wait_for "local-llm (:8102)" "http://127.0.0.1:8102/health" 900

log "starting kokoro-tts"
docker compose -f "$TTS_DIR/docker-compose.yml" up -d
wait_for "kokoro-tts (:8880)" "http://127.0.0.1:8880/health" 300

log "prod stack up"
