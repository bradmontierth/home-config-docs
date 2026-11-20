// Realtime dishwasher state detector @ 5s cadence (number-only timestamps)

// Inputs:  msg.payload = power in W (Number); optional msg.ts = epoch ms (Number)
// Outputs: msg.state ("running"|"drying"|"clean"), msg.features {...}, msg.state_since (ISO)

const SAMPLE_SECS = 5;

// ---- Thresholds (tune to your unit) ----
const CLEAN_MEAN_MAX = 25;       // W
const RUN_MEAN_MIN = 600;      // W
const RUN_HIGH_MIN = 650;      // W
const RUN_AMP_MAX = 120;      // W
const DRY_PEAK_MAX = 600;      // W
const DRY_AMP_MIN = 180;      // W
const DRY_GATE_MIN = 10;       // minutes since last plateau before drying allowed
const DRY_RECENT_WIN = 120;      // minutes lookback for “recent hi”
const DRY_CLEAN_RELEASE_MIN = 2; // minutes clean needed to exit sticky drying

const MIN_SEG_MIN = 4;        // default debounce (other transitions)
const TERMINAL_CLEAN_TAIL_SEC = 90; // quick end-of-cycle detection

// ---- NEW: fast clean->running rules ----
const INSTANT_RUN_W = 100;   // watts that clearly exceed idle/noise
const INSTANT_RUN_SEC = 30;    // seconds above that before we call it running
const CLEAN_TO_RUNNING_SEC = 30; // transition-specific debounce

// ---- Windows derived from cadence ----
const WIN_SAMPLES = Math.ceil((4 * 60) / SAMPLE_SECS); // 4 min
const RECENT_HI_SAMPLES = Math.ceil((DRY_RECENT_WIN * 60) / SAMPLE_SECS);
const CLEAN_RELEASE_SAMPLES = Math.ceil((DRY_CLEAN_RELEASE_MIN * 60) / SAMPLE_SECS);
const MIN_SEG_SAMPLES = Math.ceil((MIN_SEG_MIN * 60) / SAMPLE_SECS);
const TAIL_SAMPLES = Math.ceil(TERMINAL_CLEAN_TAIL_SEC / SAMPLE_SECS);

// ---- NEW: derived sample counts for fast path ----
const INSTANT_RUN_SAMPLES = Math.ceil(INSTANT_RUN_SEC / SAMPLE_SECS);
const CLEAN_TO_RUNNING_SAMPLES = Math.ceil(CLEAN_TO_RUNNING_SEC / SAMPLE_SECS);

// ---- Read input ----
const w = Number(msg.payload);
if (!isFinite(w)) { node.warn("payload is not a finite number"); return null; }
const nowMs = (typeof msg.ts === "number" && isFinite(msg.ts)) ? msg.ts : Date.now();

// ---- Load buffers/state from context ----
let buf = context.get("buf") || [];                 // array of {tMs:Number, w:Number, hi:Boolean}
let state = context.get("state") || "clean";
let stateSinceMs = context.get("stateSinceMs");
if (typeof stateSinceMs !== "number") {
  const legacy = context.get("stateSince");
  stateSinceMs = isFinite(Date.parse(legacy)) ? Date.parse(legacy) : nowMs;
}
let pending = context.get("pending") || null;       // {state, count}
let cleanStreak = context.get("cleanStreak") || 0;  // samples while clean during sticky drying

// ---- NEW: track consecutive “active” samples > INSTANT_RUN_W ----
let runStreak = context.get("runStreak") || 0;

// ---- Update buffer ----
const hiSample = w >= RUN_HIGH_MIN;
buf.push({ tMs: nowMs, w, hi: hiSample });
const maxBuf = RECENT_HI_SAMPLES + 10;
if (buf.length > maxBuf) buf = buf.slice(-maxBuf);

// ---- Helpers ----
function rollStats(arr, n) {
  const a = n > 0 ? arr.slice(-n) : arr.slice();
  if (a.length === 0) return { mean: 0, max: 0, min: 0, amp: 0, hiAny: false };
  let sum = 0, max = -Infinity, min = Infinity, hiAny = false;
  for (const s of a) {
    sum += s.w;
    if (s.w > max) max = s.w;
    if (s.w < min) min = s.w;
    if (s.hi) hiAny = true;
  }
  const mean = sum / a.length;
  return { mean, max, min, amp: max - min, hiAny };
}

// ---- Compute features ----
const win = rollStats(buf, WIN_SAMPLES);
const recent = rollStats(buf, RECENT_HI_SAMPLES);

// minutes since last high plateau
let minutesSinceHi = Infinity;
for (let i = buf.length - 1; i >= 0; i--) {
  if (buf[i].hi) {
    const dtSec = (nowMs - buf[i].tMs) / 1000;
    minutesSinceHi = dtSec / 60;
    break;
  }
}

// ---- Instantaneous proposal (pre-sticky, pre-smoothing) ----
const isClean = (win.mean <= CLEAN_MEAN_MAX);
const isRunning = ((win.max >= RUN_HIGH_MIN && win.amp <= RUN_AMP_MAX) || (win.mean >= RUN_MEAN_MIN)) && !isClean;
const isDrying = (!isClean && !isRunning &&
  recent.hiAny &&
  minutesSinceHi >= DRY_GATE_MIN &&
  win.max <= DRY_PEAK_MAX &&
  win.amp >= DRY_AMP_MIN &&
  win.mean > (CLEAN_MEAN_MAX + 40));

let proposed = isClean ? "clean" : (isRunning ? "running" : (isDrying ? "drying" : "running"));

// ---- NEW: fast path for clean->running based on streak over INSTANT_RUN_W ----
if (w > INSTANT_RUN_W) runStreak++; else runStreak = 0;
const instantRunning = (runStreak >= INSTANT_RUN_SAMPLES);


// ===================================================================================
// FIXED LOGIC: The order of the next two blocks has been swapped.
// The "Terminal-clean endcap" now runs BEFORE "Sticky drying". This prevents the
// endcap's general rule from interfering with the specific, stateful logic
// required to safely exit the drying state.
// ===================================================================================

// ---- Terminal-clean endcap ----
const tail = rollStats(buf, TAIL_SAMPLES);
const tailMostlyClean = (tail.mean <= CLEAN_MEAN_MAX * 1.2) && (tail.max <= Math.max(40, CLEAN_MEAN_MAX + 5));
if (tailMostlyClean) proposed = "clean";

// ---- Sticky drying ----
if (state === "drying") {
  if (win.max >= RUN_HIGH_MIN) {
    proposed = "running";
    cleanStreak = 0;
  } else if (isClean) {
    // Count and clamp at threshold so we keep proposing "clean" every tick after release
    cleanStreak = Math.min(cleanStreak + 1, CLEAN_RELEASE_SAMPLES);

    if (cleanStreak >= CLEAN_RELEASE_SAMPLES) {
      // Streak requirement met: consistently propose "clean"
      proposed = "clean";
      // IMPORTANT: do NOT reset cleanStreak here. Wait until the state actually changes.
    } else {
      proposed = "drying";
    }
  } else {
    // Small blip while drying → reset and hold drying
    cleanStreak = 0;
    proposed = "drying";
  }
} else {
  // Not in drying → streak irrelevant
  cleanStreak = 0;
}


// ---- Min-segment smoothing (transition-specific) ----
if (proposed !== state) {
  if (state === "clean" && proposed === "running" && instantRunning) {
    state = "running";
    stateSinceMs = nowMs;
    pending = null;
  } else if (state === "drying" && proposed === "clean") {
    // Release from sticky drying: commit immediately
    state = "clean";
    stateSinceMs = nowMs;
    pending = null;
    cleanStreak = 0; // now safe to reset
  } else {
    const required = (state === "clean" && proposed === "running")
      ? CLEAN_TO_RUNNING_SAMPLES
      : MIN_SEG_SAMPLES;

    if (pending && pending.state === proposed) {
      pending.count += 1;
      if (pending.count >= required) {
        state = proposed;
        stateSinceMs = nowMs;
        pending = null;
      }
    } else {
      pending = { state: proposed, count: 1 };
    }
  }
} else {
  pending = null;
}


// ---- Prepare output ----
msg.state = state;
msg.state_since = new Date(stateSinceMs).toISOString();
msg.features = {
  mean: Number(win.mean.toFixed(1)),
  max: Number(win.max.toFixed(1)),
  min: Number(win.min.toFixed(1)),
  amp: Number(win.amp.toFixed(1)),
  recent_hi: recent.hiAny,
  minutes_since_hi: minutesSinceHi,
  proposed,
  pending_count: pending ? pending.count : 0,
  // NEW: for clarity on timing
  pending_required: (state === "clean" && proposed === "running") ? CLEAN_TO_RUNNING_SAMPLES : MIN_SEG_SAMPLES,
  run_streak: runStreak,
  instant_running: instantRunning,
  clean_streak: cleanStreak // Added for easier debugging
};

// ---- Persist ----
context.set("buf", buf);
context.set("state", state);
context.set("stateSinceMs", stateSinceMs);
context.set("pending", pending);
context.set("cleanStreak", cleanStreak);
context.set("runStreak", runStreak);

return msg;