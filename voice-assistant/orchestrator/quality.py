"""Paired Cohere microphone quality and bounded pre-dispatch selection.

Shadow uploads never dispatch. The original command handler can select a paired
recording once, then retains ownership of playback and follow-up sessions.

The request context follows the existing final-ASR task, so no duplicate primary
inference is needed. SQLite and audio writes run off the response path. Rows are
keyed by the immutable /verify turn ID, not the mutable wake arbitration holder.
"""
from __future__ import annotations

import asyncio
import contextvars
import io
import json
import logging
import math
import re
import time
import wave
from array import array
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from rapidfuzz.fuzz import ratio

from . import config, db, turns, verify

log = logging.getLogger("orchestrator.quality")
router = APIRouter(prefix="/quality")
context = contextvars.ContextVar("quality_observation", default=None)
_tasks: set[asyncio.Task] = set()
ROOMS = {"kitchen", "familyroom"}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS mic_quality (
 turn_id TEXT NOT NULL, sat TEXT NOT NULL, role TEXT NOT NULL,
 received_at REAL NOT NULL, completed_at REAL NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(turn_id, sat));
CREATE INDEX IF NOT EXISTS mic_quality_at ON mic_quality(received_at DESC);
CREATE TABLE IF NOT EXISTS mic_quality_labels (
 turn_id TEXT PRIMARY KEY, expected_sat TEXT NOT NULL, notes TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mic_quality_decisions (
 turn_id TEXT PRIMARY KEY, at REAL NOT NULL, payload TEXT NOT NULL);
"""


@dataclass
class LivePair:
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    candidates: dict = field(default_factory=dict)
    sealed: bool = False
    expiry: asyncio.TimerHandle | None = None


_live: OrderedDict[str, LivePair] = OrderedDict()
LIVE_MAX = 32
LIVE_TTL_S = 180


def _expire(turn_id, pair):
    if _live.get(turn_id) is pair:
        _live.pop(turn_id)
    pair.sealed = True
    pair.candidates.clear()
    pair.ready.set()
    if pair.expiry:
        pair.expiry.cancel()


def _open_pair(turn_id):
    pair = _live.get(turn_id)
    if pair is None:
        while len(_live) >= LIVE_MAX:
            old_id, old = next(iter(_live.items()))
            _expire(old_id, old)
        pair = _live[turn_id] = LivePair()
        pair.expiry = asyncio.get_running_loop().call_later(LIVE_TTL_S, _expire, turn_id, pair)
    return pair


def mode():
    if not config.MIC_QUALITY_OBSERVE:
        return "off"
    return "select" if config.MIC_QUALITY_SELECT else "observe"


def _conn():
    conn = db.connect(config.DB_PATH)
    conn.execute("PRAGMA busy_timeout=250")
    conn.executescript(_SCHEMA)
    return conn


def enabled(sat):
    return config.MIC_QUALITY_OBSERVE and bool(config.FINAL_ASR_URL) and sat in ROOMS


def valid_pair(turn_id, sat, role, shadow_turn_id=None):
    if not enabled(sat) or not re.fullmatch(r"[a-f0-9]{32}", turn_id or ""):
        return False
    try:
        row = turns._conn().execute(
            "SELECT sat,kind,verified,at FROM turns WHERE turn_id=?", (turn_id,)).fetchone()
        if not row or row["kind"] != "wake" or not row["verified"] or time.time() - row["at"] > 180:
            return False
        if role == "primary":
            return row["sat"] == sat
        if row["sat"] not in ROOMS or row["sat"] == sat:
            return False
        loser = turns._conn().execute(
            "SELECT sat,arb_turn_id,reject_reason FROM turns WHERE turn_id=?",
            (shadow_turn_id,)).fetchone()
        return bool(loser and loser["sat"] == sat and loser["arb_turn_id"] == turn_id
                    and loser["reject_reason"] == "suppressed")
    except Exception:
        log.exception("quality pairing failed")
        return False


def begin(turn_id, sat, role, wav, shadow_turn_id=None):
    if not valid_pair(turn_id, sat, role, shadow_turn_id):
        return None
    if config.MIC_QUALITY_SELECT:
        _open_pair(turn_id)
    return {"turn_id": turn_id, "sat": sat, "role": role, "wav": wav,
            "received_at": time.time(), "recorded": False}


def acoustic(wav):
    """Command-channel level contrast, NOT calibrated speech SNR.

    Both uploads stitch processed wake pre-roll to command audio; the kitchen
    switches channels there. Use command audio only, never subtract a ch0 noise
    floor from ch1 speech. Low/high 50-ms energy percentiles are a diagnostic;
    steady speech, gain control and music can all confound this estimate.
    """
    try:
        with wave.open(io.BytesIO(wav)) as w:
            if w.getsampwidth() != 2 or w.getnchannels() != 1:
                return {}
            rate = w.getframerate()
            samples = array("h", w.readframes(w.getnframes()))
        samples = samples[int(rate * 2.5):]
        block = max(1, rate // 20)
        energies = sorted(sum(x*x for x in samples[i:i+block]) / block
                          for i in range(0, len(samples)-block+1, block))
        if len(energies) < 6:
            return {}
        low, high = energies[int((len(energies)-1)*.2)], energies[int((len(energies)-1)*.9)]
        if low <= 0 or high <= 0:
            return {}
        return {"noise_floor_dbfs": round(10*math.log10(low/32768**2), 2),
                "level_contrast_db": round(10*math.log10(high/low), 2),
                "acoustic_method": "command_50ms_p90_minus_p20_v1"}
    except (ValueError, EOFError, wave.Error):
        return {}


def complete(obs, payload=None, wall_ms=None, error=None):
    if obs is None or obs["recorded"]:
        return
    obs["recorded"] = True
    record = {**obs, "completed_at": time.time(), "asr": payload or {},
              "wall_ms": wall_ms, "error": error}
    obs["candidate"] = record
    pair = _live.get(obs["turn_id"])
    if pair is not None and not pair.sealed:
        # First completed candidate per role wins; later uploads are telemetry.
        pair.candidates.setdefault(obs["role"], record)
        if obs["role"] == "shadow":
            pair.ready.set()
    # _store removes audio bytes from its own shallow copy, not the live cache.
    task = asyncio.create_task(asyncio.to_thread(_store, dict(record)))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _store(record):
    try:
        wav = record.pop("wav")
        record.pop("recorded", None)
        record.update(acoustic(wav))
        # At most the most recent 14 days of observation audio. Text/scores
        # remain durable like the existing turns table.
        root = Path(config.DB_PATH).parent / "mic_quality_audio"
        root.mkdir(exist_ok=True)
        path = root / f'{record["turn_id"]}-{record["sat"]}.wav'
        path.write_bytes(wav)
        record["audio"] = path.name
        with _conn() as conn:
            conn.execute("INSERT OR REPLACE INTO mic_quality VALUES (?,?,?,?,?,?)",
                         (record["turn_id"], record["sat"], record["role"],
                          record["received_at"], record["completed_at"], json.dumps(record)))
        conn.close()
        cutoff = time.time() - 14*86400
        for old in root.glob("*.wav"):
            if old.stat().st_mtime < cutoff:
                old.unlink(missing_ok=True)
    except Exception:
        log.exception("quality observation write failed")


def normalized_command(candidate):
    text = candidate["asr"].get("transcript_text", "")
    found, command, _ = verify.verify_and_extract(text)
    return " ".join(re.findall(r"\w+", (command if found else text).lower()))


def comparison(candidates):
    """Uncalibrated score ranking for human review; never an active decision."""
    by_role = {c["role"]: c for c in candidates}
    primary, shadow = by_role.get("primary"), by_role.get("shadow")
    if not primary or not shadow:
        return {"reason": "waiting_for_pair", "proposed_sat": None}
    p, s = [c["asr"].get("summary", {}) for c in (primary, shadow)]
    scores = [v.get("confidence") for v in (p, s)]
    if any(not v.get("confidence_valid") for v in (p, s)) or any(
        isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) or x > 0 for x in scores
    ):
        return {"reason": "missing_or_invalid_confidence", "proposed_sat": None}
    if not primary["asr"].get("model") or p.get("confidence_method") != "mean_token_logprob_v1" or primary["asr"].get("model") != shadow["asr"].get("model") or any(
        p.get(k) != s.get(k) for k in ("confidence_method", "decoder", "bias_weight", "bias_signature")
    ):
        return {"reason": "incomparable_decoders", "proposed_sat": None}
    gap = scores[1] - scores[0]
    return {"reason": "uncalibrated_score_ranking", "score_gap_shadow_minus_primary": gap,
            "proposed_sat": None if abs(gap) < 1e-6 else (shadow if gap > 0 else primary)["sat"],
            "agreement_ratio": ratio(normalized_command(primary), normalized_command(shadow)),
            "shadow_ready_delta_ms": round(1000*(shadow["completed_at"]-primary["completed_at"]))}



def evaluate(primary, shadow, *, margin, sanity_check):
    """Return a reason + candidate. Exact text agreement, never fuzzy ≥90:
    similar numbers/negations can carry very different commands.
    """
    report = comparison([primary, shadow])
    if report["reason"] != "uncalibrated_score_ranking":
        return report["reason"], None, report
    p_text, s_text = normalized_command(primary), normalized_command(shadow)
    if not p_text or not s_text:
        return "empty_command", None, report
    if p_text == s_text:
        return "transcripts_agree", None, report
    if report["score_gap_shadow_minus_primary"] <= margin:
        return "original_score_or_small_gap", None, report
    reason = sanity_check(shadow["asr"].get("transcript_text", ""),
                          primary["asr"].get("transcript_text", ""))
    if reason:
        return "shadow_rejected_" + reason, None, report
    return "better_shadow", shadow, report


def _has_peer(turn_id, sat):
    rows = turns._conn().execute(
        "SELECT sat FROM turns WHERE arb_turn_id=? AND reject_reason='suppressed'",
        (turn_id,)).fetchall()
    return any(row["sat"] in ROOMS and row["sat"] != sat for row in rows)


def _save_decision(decision):
    try:
        with _conn() as conn:
            conn.execute("INSERT OR IGNORE INTO mic_quality_decisions VALUES (?,?,?)",
                         (decision["turn_id"], time.time(), json.dumps(decision)))
        conn.close()
    except Exception:
        log.exception("quality decision write failed")


async def select_candidate(obs, *, text, model, fallback_reason, sanity_check):
    """At most one pre-dispatch choice, never a late action or second decode.

    Only a validated peer's suppressed wake earns a wait budget. No database
    reads of audio, no disk/network I/O or inference on the selection path.
    """
    if obs is None or not config.MIC_QUALITY_SELECT:
        return None
    started = time.monotonic()
    selected = None
    pair = _live.get(obs["turn_id"])
    decision = {"turn_id": obs["turn_id"], "mode": "select",
                "rule": "cohere_disagreement_v1", "owner_sat": obs["sat"],
                "selected_sat": obs["sat"], "selected_text": text,
                "margin": config.MIC_QUALITY_MARGIN,
                "wait_cap_ms": config.MIC_QUALITY_WAIT_MS, "wait_ms": 0,
                "reason": "missing_primary"}
    try:
        primary = obs.get("candidate")
        if not primary or primary.get("error") or not primary.get("asr"):
            return None
        # Never undo the existing hedge's rejection/fallback, or compare a
        # Parakeet final result with a Cohere score from a discarded hypothesis.
        if fallback_reason not in (None, "none") or model != primary["asr"].get("model"):
            decision["reason"] = "primary_fallback"
            return None
        if not pair or pair.sealed:
            decision["reason"] = "pair_unavailable"
            return None
        if not _has_peer(obs["turn_id"], obs["sat"]):
            decision["reason"] = "no_peer"
            return None
        if "shadow" not in pair.candidates:
            wait_start = time.monotonic()
            try:
                await asyncio.wait_for(pair.ready.wait(), config.MIC_QUALITY_WAIT_MS / 1000)
            except asyncio.TimeoutError:
                decision["reason"] = "shadow_timeout"
                return None
            finally:
                decision["wait_ms"] = round(1000 * (time.monotonic() - wait_start))
        shadow = pair.candidates.get("shadow")
        if not shadow or pair.sealed:
            decision["reason"] = "shadow_unavailable"
            return None
        if shadow.get("error"):
            decision["reason"] = "shadow_error"
            return None
        reason, selected, report = evaluate(primary, shadow,
            margin=config.MIC_QUALITY_MARGIN, sanity_check=sanity_check)
        decision.update(reason=reason, comparison=report)
        if selected:
            decision.update(selected_sat=selected["sat"],
                            selected_text=selected["asr"]["transcript_text"],
                            selected_asr_ms=selected["wall_ms"])
        return selected
    except asyncio.CancelledError:
        decision["reason"] = "cancelled"
        raise
    except Exception:
        log.exception("quality picker failed; retaining original")
        decision["reason"] = "picker_error"
        return None
    finally:
        # Retain a small sealed marker until expiry; a late shadow still logs
        # its audio/score but cannot reopen or alter this decision.
        if pair is not None:
            pair.sealed = True
            pair.candidates.clear()
        decision["selection_ms"] = round(1000 * (time.monotonic() - started))
        log.info("mic selection turn=%s owner=%s selected=%s reason=%s wait=%sms",
                 decision["turn_id"], decision["owner_sat"], decision["selected_sat"],
                 decision["reason"], decision["wait_ms"])
        task = asyncio.create_task(asyncio.to_thread(_save_decision, decision))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


def recent(limit=50):
    with _conn() as conn:
        ids = conn.execute("SELECT turn_id,MIN(received_at) AS at FROM mic_quality GROUP BY turn_id ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
        result = []
        for row in ids:
            candidates = [json.loads(r[0]) for r in conn.execute("SELECT payload FROM mic_quality WHERE turn_id=? ORDER BY role", (row["turn_id"],))]
            label = conn.execute("SELECT expected_sat,notes FROM mic_quality_labels WHERE turn_id=?", (row["turn_id"],)).fetchone()
            selection = conn.execute("SELECT payload FROM mic_quality_decisions WHERE turn_id=?", (row["turn_id"],)).fetchone()
            actual = conn.execute("SELECT sat,command,asr_model,asr_fallback_reason FROM turns WHERE turn_id=?", (row["turn_id"],)).fetchone()
            result.append({"turn_id": row["turn_id"], "at": row["at"], "candidates": candidates,
                           "actual": dict(actual) if actual else None,
                           "selection": json.loads(selection[0]) if selection else None,
                           "label": dict(label) if label else None, **comparison(candidates)})
    conn.close()
    return result


@router.get("/recent")
async def get_recent(limit: int = 50):
    return {"mode": mode(),
            "turns": await asyncio.to_thread(recent, min(100, max(1, limit)))}


class Label(BaseModel):
    expected_sat: str
    notes: str = ""


@router.post("/{turn_id}/label")
async def label(turn_id: str, body: Label):
    if body.expected_sat not in ROOMS | {"either", "neither"} or len(body.notes) > 1000:
        raise HTTPException(400, "invalid label")
    def save():
        with _conn() as conn:
            if not conn.execute("SELECT 1 FROM mic_quality WHERE turn_id=?", (turn_id,)).fetchone():
                raise HTTPException(404, "unknown observation")
            conn.execute("INSERT OR REPLACE INTO mic_quality_labels VALUES (?,?,?)", (turn_id, body.expected_sat, body.notes))
        conn.close()
    await asyncio.to_thread(save)
    return {"ok": True}


@router.get("/audio/{name}")
def audio(name: str):
    if not re.fullmatch(r"[a-f0-9]{32}-(kitchen|familyroom)\.wav", name):
        raise HTTPException(404)
    path = Path(config.DB_PATH).parent / "mic_quality_audio" / name
    if not path.is_file():
        raise HTTPException(404, "audio expired or unavailable")
    return FileResponse(path, media_type="audio/wav")


@router.get("/ui", response_class=HTMLResponse)
def ui():
    return (Path(__file__).parent / "quality.html").read_text()
