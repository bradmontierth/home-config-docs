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

# --- weather (Home Assistant REST) -----------------------------------------
# HA runs on this same host; network_mode host makes 127.0.0.1 work in-container.
HA_URL = os.getenv("HA_URL", "http://127.0.0.1:8123").rstrip("/")
# Long-lived token file (raw, or dotenv HA_TOKEN=…) — mount from cecret_lake.
HA_TOKEN_FILE = os.getenv("HA_TOKEN_FILE", "/secrets/ha_token")
# Local weather-station sensors (accurate on-site readings) + the met.no
# entity that backs the dashboard's condition + forecast strip.
WEATHER_ENTITY = os.getenv("WEATHER_ENTITY", "weather.forecast_home_2")
OUTDOOR_TEMP_ENTITY = os.getenv(
    "OUTDOOR_TEMP_ENTITY", "sensor.weather_station_outdoor_temperature")
WIND_SPEED_ENTITY = os.getenv(
    "WIND_SPEED_ENTITY", "sensor.weather_station_wind_speed")

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
# NOTE: on reasoning models this cap covers reasoning AND the answer — 700 was
# starving deliberation entirely (~69 reasoning tokens, one-glance search reads).
OPENROUTER_MAX_TOKENS = int(os.getenv("OPENROUTER_MAX_TOKENS", "4000"))
# Reasoning effort for the smart model (empty string disables the param).
# "medium" lets it think about search results and re-search; the spoken filler
# masks the added seconds.
OPENROUTER_REASONING_EFFORT = os.getenv("OPENROUTER_REASONING_EFFORT", "medium")
# Sentinel separating the short spoken answer from the full dashboard answer.
ASK_SENTINEL = os.getenv("ASK_SENTINEL", "===MORE===")
# Web search via OpenRouter's web plugin. "native" passes through to the
# provider's own search tool (OpenAI: model decides per-query whether to
# search, so no-search questions stay fast). Set OPENROUTER_WEB_SEARCH=0 to
# disable. "exa" would search on EVERY request — don't use it here.
OPENROUTER_WEB_SEARCH = os.getenv("OPENROUTER_WEB_SEARCH", "1") not in (
    "0", "false", "no", ""
)
OPENROUTER_WEB_ENGINE = os.getenv("OPENROUTER_WEB_ENGINE", "native")
# Household timezone, stamped into the ask system prompt so the model can
# resolve "today" / "last night" (the container itself runs UTC).
ASK_TIMEZONE = os.getenv("ASK_TIMEZONE", "America/Denver")
# How much page content each search returns to the model (low/medium/high).
# high costs a cent or two more per search but thin results made the model
# confidently report "no game yesterday" on a day with two matches.
OPENROUTER_WEB_CONTEXT = os.getenv("OPENROUTER_WEB_CONTEXT", "high")

# --- business hours (Google Places API (New)) ------------------------------
# Server-side key file (raw key, or GOOGLE_PLACES_KEY=...) mounted read-only
# from cecret_lake. HOME_LAT/LON bias a Text Search to nearby locations.
GOOGLE_PLACES_KEY_FILE = os.getenv("GOOGLE_PLACES_KEY_FILE", "")
HOME_LAT = float(os.getenv("HOME_LAT", "0"))
HOME_LON = float(os.getenv("HOME_LON", "0"))
PLACES_LOCATION_RADIUS_M = float(os.getenv("PLACES_LOCATION_RADIUS_M", "16093.44"))
PLACES_MAX_RESULTS = int(os.getenv("PLACES_MAX_RESULTS", "8"))
# Google Cloud is hard-capped at 25 SearchText calls/day. Stay below that here
# too, while leaving five calls of headroom for tests and enforcement lag.
PLACES_DAILY_LIMIT = int(os.getenv("PLACES_DAILY_LIMIT", "20"))
PLACES_CACHE_TTL_S = float(os.getenv("PLACES_CACHE_TTL_S", "86400"))
# Spoken filler ("Let me look that up…") pushed to the satellite the moment an
# ask heads to the smart model, masking the 3-7s round trip. Pre-rendered WAVs;
# playback serializes behind the satellite's PLAYBACK_LOCK. Empty URL disables.
ASK_FILLER = os.getenv("ASK_FILLER", "1") not in ("0", "false", "no", "")
SATELLITE_SPEAK_URL = os.getenv(
    "SATELLITE_SPEAK_URL", "http://192.168.10.24:8781/speak"
)
# Follow-up context: recent ask Q+A pairs replayed to the smart model so
# "but who's playing in it?" works. Turns kept / freshness window.
ASK_HISTORY_TURNS = int(os.getenv("ASK_HISTORY_TURNS", "4"))
ASK_HISTORY_TTL_S = int(os.getenv("ASK_HISTORY_TTL_S", "300"))
# How long "show that answer again" can recall the last answer after the
# popup auto-hides (deliberately much longer than the follow-up history TTL).
ASK_RECALL_TTL_S = int(os.getenv("ASK_RECALL_TTL_S", "1800"))

# --- lists (todo / shopping / reminders via voice-notes companion) --------
# The companion is note-centric: sync a note then analyze it to extract typed
# items. GET /api/items lists; /complete + DELETE mutate. See lists.py.
COMPANION_URL = os.getenv("COMPANION_URL", "http://192.168.10.217:8768")
# Lists are SHARED (the kitchen is one household) — reads span every companion
# user. But the companion requires a valid owner on write, so newly added items
# are filed under this account. It's attribution only; display never filters by
# it. Must be a known companion user (brad/adrienne).
LIST_OWNER = os.getenv("LIST_OWNER", "brad")
# rapidfuzz score (0-100) an item must clear to be matched for completion.
LIST_MATCH_THRESHOLD = float(os.getenv("LIST_MATCH_THRESHOLD", "70"))

# --- music (Music Assistant) ------------------------------------------------
# MA server (Beelink). API is WebSocket JSON-RPC at /ws via the official
# music-assistant-client package — there is no REST search. See music.py.
MA_URL = os.getenv("MA_URL", "http://192.168.10.217:8095")
# The kitchen jukebox player/queue (squeezelite Kitchen-Big-Speakers) — same
# queue the NFC reader drives, so voice and cards share one player.
MA_QUEUE_ID = os.getenv("MA_QUEUE_ID", "e4:5f:01:67:1e:56")
# The NFC jukebox drives the SAME queue and pause-toggles a re-scan of the
# card it thinks is playing. When voice replaces the queue content, ping this
# so it drops that marker — otherwise scanning the last card pause-toggles the
# voice-chosen music instead of playing the card. Empty disables.
JUKEBOX_EXTERNAL_PLAY_URL = os.getenv(
    "JUKEBOX_EXTERNAL_PLAY_URL", "http://192.168.10.217:8769/api/external-play"
)
# Ducking: on a wake trigger / alarm, music volume drops to cur*FACTOR (but at
# least MIN). TTL is the watchdog that restores volume if the satellite dies
# mid-turn and its unduck never arrives.
MUSIC_DUCK_FACTOR = float(os.getenv("MUSIC_DUCK_FACTOR", "0.25"))
MUSIC_DUCK_MIN = int(os.getenv("MUSIC_DUCK_MIN", "5"))
MUSIC_DUCK_TTL_S = float(os.getenv("MUSIC_DUCK_TTL_S", "240"))
# In-memory library-name index for the ASR-robust fuzzy resolver (music.py);
# refreshed in the background when older than this.
MUSIC_INDEX_TTL_S = float(os.getenv("MUSIC_INDEX_TTL_S", "900"))

# --- wake stage-2 ----------------------------------------------------------
WAKE_PHRASE = os.getenv("WAKE_PHRASE", "okay computer")
# All phrases the verifier accepts (dual wake: okay_google model on the
# satellite; "hey google" covers the common spoken variant). Comma-separated.
WAKE_PHRASES = [p.strip() for p in os.getenv(
    "WAKE_PHRASES", f"{WAKE_PHRASE},okay google,hey google"
).split(",") if p.strip()]
# rapidfuzz partial_ratio (0-100) the wake phrase must clear in the transcript.
WAKE_FUZZ_THRESHOLD = float(os.getenv("WAKE_FUZZ_THRESHOLD", "80"))
# Overlap rescue: if the full pre-roll decode rejects, re-decode only the last
# N seconds and accept if EITHER verifies. With two voices in the pre-roll,
# Parakeet (single-speaker ASR) transcribes the established/dominant stream and
# drops the overlapped wake phrase entirely (diagnosed 2026-07-12: rejects like
# "Joe, did you see the" while stage-1 scored 0.67 on the same audio). The tail
# decode denies the competing stream the lead-in context it latches onto. The
# phrase itself takes ~0.8-1.0s and stage-1 can fire slightly before it ends,
# so keep >= 1.5; 0 disables.
VERIFY_TAIL_S = float(os.getenv("VERIFY_TAIL_S", "1.5"))

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
