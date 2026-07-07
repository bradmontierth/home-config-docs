"""Environment-driven configuration for the voice orchestrator.

All service URLs default to the live homelab endpoints (see
voice-assistant-plan.md "Existing Components"). Override via env for testing.
"""

from __future__ import annotations

import os


# --- downstream services ---------------------------------------------------
# Parakeet ASR (GX10): batch transcribe, raw WAV body, ?chunk/context params.
ASR_URL = os.getenv("ASR_URL", "http://192.168.10.187:8090/parakeet/transcribe")
# qwen3-next LLM (GX10), OpenAI-compatible; respects enable_thinking=false.
LLM_URL = os.getenv("LLM_URL", "http://192.168.10.187:8095/v1/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3-next")
# TTS router (Beelink) → Kokoro fast path. OpenAI /audio/speech shape.
TTS_URL = os.getenv("TTS_URL", "http://192.168.10.217:8891/v1/audio/speech")
TTS_VOICE = os.getenv("TTS_VOICE", "fast:doorbell")

# Dashboard fan-out: the kiosk connects to dashboard_webapp's /api/live; we POST
# assistant events to this endpoint which re-broadcasts them (jukebox pattern).
DASHBOARD_EVENT_URL = os.getenv(
    "DASHBOARD_EVENT_URL", "http://192.168.10.217:8777/api/assistant/event"
)
DASHBOARD_EVENT_TOKEN = os.getenv("DASHBOARD_EVENT_TOKEN", "")

# Satellite alarm playback (kitchen-speaker). Not built yet in this slice; the
# orchestrator POSTs best-effort and logs on failure. Contract documented in
# README. Empty string disables the call.
SATELLITE_ALARM_URL = os.getenv(
    "SATELLITE_ALARM_URL", "http://192.168.10.24:8781/alarm"
)

# --- knowledge / ask mode (smart model via OpenRouter) --------------------
# Streaming, short-first (===MORE=== sentinel). Only the `ask` intent hits this.
OPENROUTER_BASE_URL = os.getenv(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
)
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-5.4")
# Path to a file holding the key (raw, or a dotenv line OPENROUTER_KEY=...).
OPENROUTER_KEY_FILE = os.getenv("OPENROUTER_API_KEY_FILE", "")
# Ceiling on generated tokens. Also keeps OpenRouter's pre-auth cost check happy
# when the account balance is low (unbounded max_tokens can trip a 402).
OPENROUTER_MAX_TOKENS = int(os.getenv("OPENROUTER_MAX_TOKENS", "700"))
# Sentinel separating the short spoken answer from the full dashboard answer.
ASK_SENTINEL = os.getenv("ASK_SENTINEL", "===MORE===")

# --- wake stage-2 ----------------------------------------------------------
WAKE_PHRASE = os.getenv("WAKE_PHRASE", "okay computer")
# rapidfuzz partial_ratio (0-100) the wake phrase must clear in the transcript.
WAKE_FUZZ_THRESHOLD = float(os.getenv("WAKE_FUZZ_THRESHOLD", "80"))

# --- timers ----------------------------------------------------------------
DB_PATH = os.getenv("ORCH_DB_PATH", "/home/pi/voice-orchestrator/data/orchestrator.db")
# Pre-rendered timer announcement WAVs (instant, GX10-independent alarm audio).
ANNOUNCE_CACHE_DIR = os.getenv(
    "ANNOUNCE_CACHE_DIR", "/home/pi/voice-orchestrator/data/announce"
)

# Valid sound themes the LLM may choose from (CC0 clips live on the satellite).
SOUND_THEMES = (
    "cluck",        # chicken / poultry
    "moo",          # beef / dairy
    "sizzle",       # frying / searing
    "steam_whistle",  # boiling / pasta / rice
    "bubbling",     # simmering / sauce
    "oven_ding",    # baking / roasting
    "marimba",      # neutral fallback
)
DEFAULT_THEME = "marimba"

HTTP_PORT = int(os.getenv("ORCH_HTTP_PORT", "8785"))
