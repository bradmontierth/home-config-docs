const HIGH_WATTS = 100;
const REQUIRED_HIGH_SAMPLES = 2;
const REARM_LOW_WATTS = 20;
const REARM_LOW_MS = 2 * 60 * 1000;

function boolish(value) {
  if (value === true || value === 1 || value === "1") return true;
  const normalized = String(value ?? "").trim().toLowerCase();
  return normalized === "on" || normalized === "true";
}

const now = Date.now();
const watts = Number(msg.payload?.col8);
const gridOutageRaw = msg.gridOutage;
const gridOutage = boolish(gridOutageRaw);
let state = context.get("dryerOutageGuard") || {
  consecutiveHigh: 0,
  alerted: false,
  lowSince: null,
  lastAlertAt: null
};

if (!Number.isFinite(watts)) {
  state.consecutiveHigh = 0;
  context.set("dryerOutageGuard", state);
  node.status({ fill: "grey", shape: "ring", text: "dryer watts unavailable" });
  return [null, null, null];
}

// Restoration fully re-arms the detector so a running dryer will be caught if
// a new outage begins.
if (!gridOutage) {
  state.consecutiveHigh = 0;
  state.alerted = false;
  state.lowSince = null;
  context.set("dryerOutageGuard", state);
  node.status({ fill: "green", shape: "ring", text: `grid on; dryer ${Math.round(watts)}W` });
  return [null, null, null];
}

if (watts >= HIGH_WATTS) {
  state.consecutiveHigh += 1;
  state.lowSince = null;
} else {
  state.consecutiveHigh = 0;

  if (watts < REARM_LOW_WATTS) {
    if (!state.lowSince) state.lowSince = now;
    if (state.alerted && (now - state.lowSince) >= REARM_LOW_MS) {
      state.alerted = false;
      state.lastAlertAt = null;
    }
  } else {
    state.lowSince = null;
  }
}

if (state.alerted || state.consecutiveHigh < REQUIRED_HIGH_SAMPLES) {
  context.set("dryerOutageGuard", state);
  const statusText = state.alerted
    ? `outage alert latched; ${Math.round(watts)}W`
    : `outage sample ${state.consecutiveHigh}/${REQUIRED_HIGH_SAMPLES}; ${Math.round(watts)}W`;
  node.status({ fill: state.alerted ? "red" : "yellow", shape: "ring", text: statusText });
  return [null, null, null];
}

state.alerted = true;
state.lastAlertAt = now;
context.set("dryerOutageGuard", state);

const title = "Dryer running during grid outage";
const text = `The dryer is drawing ${Math.round(watts)} watts while utility grid power is out. Please turn it off now to protect the Powerwalls.`;
const devices = [global.get("BradPhoneNM"), global.get("AdriennePhoneNM")]
  .filter((device, index, all) => device && all.indexOf(device) === index);

const pushMessages = devices.map(device => ({
  ...msg,
  title,
  priority: 1,
  device,
  payload: text
}));

if (!pushMessages.length) {
  node.warn("Dryer outage detected, but no Pushover devices are configured.");
}

const audio = {
  ...msg,
  alexa: "Warning. The dryer is running while utility grid power is out. Please turn the dryer off now to protect the Powerwalls.",
  players: ["media_player.loft"],
  voice: "picard:calm",
  volume: 55
};

const diagnostic = {
  ...msg,
  payload: {
    reason: "sustained dryer load during grid outage",
    detectedAt: new Date(now).toISOString(),
    dryerWatts: watts,
    thresholdWatts: HIGH_WATTS,
    requiredHighSamples: REQUIRED_HIGH_SAMPLES,
    gridOutage,
    gridOutageRaw,
    pushoverDevices: devices,
    announcementTargets: ["media_player.loft", "kitchen_big_speakers"]
  }
};

node.status({ fill: "red", shape: "dot", text: `ALERT ${Math.round(watts)}W; grid out` });
return [pushMessages.length ? pushMessages : null, audio, diagnostic];
