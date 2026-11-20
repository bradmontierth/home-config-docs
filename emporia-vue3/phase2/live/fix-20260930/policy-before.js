// Only the selected Vue adapter can invoke the charging policy.
if(!msg.vueInputs)return null;
if(Date.now()-msg.vueAt>15000)return [{vueHold:true},null];
const overrides=msg.evChargeOverrides||{},outage=msg.gridOutage===true;
const disabled=overrides['input_boolean.tesla_charge_automation_disable']==='on';
if(disabled&&!outage){globalThis._staleSince=null;return [{vueYield:true},null];}
const before=context.get('evChargeState')||{},lastApplied=flow.get('vueEvApplied');
const ownsSolar=['solar','solar_hold_min','hold_min_runtime'].includes(lastApplied?.mode||before.lastMode);
function staleSolarHold(){
 if(globalThis._staleSince==null)globalThis._staleSince=Date.now();
 before.solarEligibleSince=null;context.set('evChargeState',before);
 if(ownsSolar&&Date.now()-globalThis._staleSince>=30000){msg.payload={state:'disabled'};msg.url='http://192.168.30.121/override';msg.method='POST';msg.headers={'Content-Type':'application/json'};msg.vueSafetyStop=true;msg.debug={result:{mode:'stale_solar_stop',reason:'Required telemetry invalid for 30s'}};return [msg,null];}
 return [{vueHold:true},null];
}
const controlsKnown=['automation_disable','asap','tonight'].every(s=>['on','off'].includes(overrides['input_boolean.tesla_charge_'+s]));
if(!outage&&(msg.gridOutage!==false||!controlsKnown))return staleSolarHold();
const lowSocRecovery=['Tesla_3_Data','Tesla_Y_Data'].some(p=>{const soc=global.get(p+'.battery_level'),limit=global.get(p+'.charge_limit_soc');return [true,'true','on',1].includes(global.get(p+'.plugged_in'))&&global.get(p+'.geofence')==='Home'&&soc!==null&&soc!==undefined&&soc!==''&&Number.isFinite(Number(soc))&&Number(soc)<30&&limit!==null&&limit!==undefined&&limit!==''&&Number.isFinite(Number(limit))&&Number(soc)<Number(limit);});
const manualIntent=overrides['input_boolean.tesla_charge_asap']==='on'||overrides['input_boolean.tesla_charge_tonight']==='on'||before.recoveryActive||lowSocRecovery;
const localNow=new Date(),localMinute=localNow.getHours()*60+localNow.getMinutes();
const hardTou=localMinute>=1080&&localMinute<1325;
if((!msg.vueTuple?.ok||!Number.isFinite(msg.pw))&&!outage&&!manualIntent&&!hardTou)return staleSolarHold();
globalThis._staleSince=null;

/**
 * Combined EV charging controller for Node-RED
 *
 * What this does:
 * - Selects the Tesla that is plugged in at Home
 * - Blocks charging during TOU window (default 6:00pm-10:05pm)
 * - Honors:
 *     input_boolean.tesla_charge_automation_disable
 *     input_boolean.tesla_charge_tonight
 *     input_boolean.tesla_charge_asap
 * - If battery is below LOW_SOC, charges up to RECOVERY_TARGET_SOC
 * - Uses excess solar when Powerwall SOC is high enough
 * - Adds hysteresis so PW SOC does not flap around 90%
 * - Requires excess solar to be present for a minimum time before starting
 * - Once solar charging starts, enforces a minimum run time to avoid relay chatter
 * - If low-SOC recovery starts, charges at full rate until the recovery target
 * - For "charge tonight", computes minimum amps needed to finish by configured morning deadline
 *
 * Assumptions:
 * - Function runs repeatedly, e.g. every 15-60 seconds
 * - Home Assistant override booleans are persisted in flow context by an upstream function node
 * - Solar/home load values are passed in msg as in your existing flow
 * - Charger accepts 0A to stop or 6-32A to charge
 */

// ---------------------------
// Configuration
// ---------------------------
const CFG = {
    VOLTS: 240,
    CHARGER_MIN_AMPS: 6,
    CHARGER_MAX_AMPS: 32,

    // Powerwall solar gating with hysteresis
    PW_START_THRESHOLD: 91,   // start solar charging only when above this
    PW_STOP_THRESHOLD: 89,    // once charging, allow down to this before solar is considered "not healthy"

    // Debounce / anti-chatter
    MIN_SOLAR_START_SECONDS: 120,   // require sustained excess solar before starting
    MIN_CHARGE_RUN_SECONDS: 1200,   // once started, keep charging for at least this long (20 min)

    // TOU block window
    TOU_START_HOUR: 18,       // 6pm
    TOU_START_MINUTE: 0,
    TOU_END_HOUR: 22,         // 10pm
    TOU_END_MINUTE: 5,        // slight buffer after 10pm

    // Overnight schedule defaults
    NIGHT_START_HOUR: 22,     // 10pm
    NIGHT_START_MINUTE: 5,
    MORNING_END_HOUR: 8,      // 8am
    MORNING_END_MINUTE: 0,

    // Battery policy
    LOW_SOC: 30,
    RECOVERY_TARGET_SOC: 38,

    // Powerwall reserve protection
    POWERWALL_SITE_ID: "2252499851100435",
    POWERWALL_DEFAULT_RESERVE_PERCENT: 20,
    POWERWALL_RESERVE_RESET_HOUR: 17,
    POWERWALL_RESERVE_RESET_MINUTE: 55,

    // Charging efficiency and capacities
    CHARGE_EFFICIENCY: 0.85,
    MODEL3_KWH: 60,
    MODELY_KWH: 74
};

// ---------------------------
// Helpers
// ---------------------------
function num(v, d = 0) {
    const n = Number(v);
    return Number.isFinite(n) ? n : d;
}

function finiteOrNull(v) {
    if(v === null || v === undefined || v === "" || typeof v === "boolean") return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
}

function boolish(v) {
    return v === true || v === "true" || v === "on" || v === "ON" || v === 1 || v === "1";
}

function clamp(v, min, max) {
    return Math.max(min, Math.min(max, v));
}

function normalizeToValidAmps(a, round = Math.floor) {
    if (!Number.isFinite(a)) return 0;
    a = round(a);
    if (a <= 0) return 0;
    if (a < CFG.CHARGER_MIN_AMPS) return CFG.CHARGER_MIN_AMPS;
    if (a > CFG.CHARGER_MAX_AMPS) return CFG.CHARGER_MAX_AMPS;
    return a;
}

function floorToValidAmps(a) {
    return normalizeToValidAmps(a, Math.floor);
}

function ceilToValidAmps(a) {
    return normalizeToValidAmps(a, Math.ceil);
}

function getState(path, fallback) {
    const v = global.get(path);
    return v === undefined || v === null ? fallback : v;
}

function getFlowState(path, fallback) {
    if (typeof flow === "undefined" || !flow) return fallback;
    const v = flow.get(path);
    return v === undefined || v === null ? fallback : v;
}

function extractStateValue(v) {
    if (v === undefined || v === null) return undefined;
    if (typeof v === "object" && v.state !== undefined) return v.state;
    return v;
}

function getIncomingEntityId(msg) {
    return msg.entity_id
        || msg.data?.entity_id
        || msg.payload?.entity_id
        || null;
}

function getIncomingEntityState(msg) {
    if (msg.data?.state !== undefined) return msg.data.state;
    if (msg.payload?.state !== undefined) return msg.payload.state;
    if (typeof msg.payload === "string" || typeof msg.payload === "boolean" || typeof msg.payload === "number") {
        return msg.payload;
    }
    return undefined;
}

function getEntityBool(msg, key, entityId, flowOverrides, incomingEntityId, incomingEntityState) {
    const msgOverrides = msg.evChargeOverrides || msg.chargeOverrides || {};

    // Upstream helper can attach the preserved states directly on msg.
    if (msgOverrides[entityId] !== undefined) {
        return boolish(extractStateValue(msgOverrides[entityId]));
    }

    // Primary source: flow context populated by the combined state-change branch.
    if (flowOverrides[entityId] !== undefined) {
        return boolish(extractStateValue(flowOverrides[entityId]));
    }

    // Explicit msg property still wins if upstream injects a clean boolean or state string.
    if (msg[key] !== undefined) return boolish(extractStateValue(msg[key]));

    // If this invocation was triggered directly by the HA entity, honor it immediately.
    if (incomingEntityId === entityId && incomingEntityState !== undefined) {
        return boolish(extractStateValue(incomingEntityState));
    }

    return false;
}

function minutesSinceMidnight(d) {
    return d.getHours() * 60 + d.getMinutes();
}

function inTouWindow(now) {
    const nowMin = minutesSinceMidnight(now);
    const start = CFG.TOU_START_HOUR * 60 + CFG.TOU_START_MINUTE;
    const end = CFG.TOU_END_HOUR * 60 + CFG.TOU_END_MINUTE;
    return nowMin >= start && nowMin < end;
}

function beforePowerwallReserveReset(now) {
    const nowMin = minutesSinceMidnight(now);
    const resetStart = CFG.POWERWALL_RESERVE_RESET_HOUR * 60 + CFG.POWERWALL_RESERVE_RESET_MINUTE;
    return nowMin < resetStart;
}

function canRaisePowerwallReserve(now) {
    return !inTouWindow(now) && beforePowerwallReserveReset(now);
}

function isAfterNightStart(now) {
    const nowMin = minutesSinceMidnight(now);
    const start = CFG.NIGHT_START_HOUR * 60 + CFG.NIGHT_START_MINUTE;
    return nowMin >= start;
}

function isBeforeMorningEnd(now) {
    const nowMin = minutesSinceMidnight(now);
    const end = CFG.MORNING_END_HOUR * 60 + CFG.MORNING_END_MINUTE;
    return nowMin < end;
}

function inOvernightWindow(now) {
    // overnight means after start tonight OR before morning end tomorrow
    return isAfterNightStart(now) || isBeforeMorningEnd(now);
}

function hoursUntilDeadline(now) {
    const deadline = new Date(now);
    deadline.setHours(CFG.MORNING_END_HOUR, CFG.MORNING_END_MINUTE, 0, 0);

    if (now.getTime() >= deadline.getTime()) {
        deadline.setDate(deadline.getDate() + 1);
    }

    return Math.max((deadline.getTime() - now.getTime()) / 3600000, 0.1);
}

function hoursUntilWindowStart(now) {
    const start = new Date(now);
    start.setHours(CFG.NIGHT_START_HOUR, CFG.NIGHT_START_MINUTE, 0, 0);

    if (now.getTime() >= start.getTime()) {
        start.setDate(start.getDate() + 1);
    }

    return Math.max((start.getTime() - now.getTime()) / 3600000, 0);
}

function calcAmpsForTarget(currentSoc, targetSoc, capacityKwh, now) {
    if (targetSoc <= currentSoc) return 0;

    const currentKwh = capacityKwh * currentSoc / 100;
    const targetKwh = capacityKwh * targetSoc / 100;
    const neededKwhDc = Math.max(targetKwh - currentKwh, 0);
    const neededKwhAc = neededKwhDc / CFG.CHARGE_EFFICIENCY;

    const hrs = hoursUntilDeadline(now);
    const wattsNeeded = (neededKwhAc / hrs) * 1000;
    const ampsNeeded = wattsNeeded / CFG.VOLTS;

    return ceilToValidAmps(ampsNeeded);
}

// ---------------------------
// Inputs from your flow
// ---------------------------
const now = new Date();
const incomingEntityId = getIncomingEntityId(msg);
const incomingEntityState = getIncomingEntityState(msg);
const flowOverrides = getFlowState("evChargeOverrides", {});
const gridOutageRaw = extractStateValue(msg.gridOutage);
const gridOutage = boolish(gridOutageRaw);

const cachedInputs = context.get("evChargeInputs") || {};

const solarPW = finiteOrNull(msg.solarPW);
const pw = finiteOrNull(msg.pw);
const evChargingPower = finiteOrNull(msg.iotawatt_ev_charging_power);
const totalHome = finiteOrNull(msg.total_home_and_heat_pump);

const telemetryReady = msg.vueTuple?.ok === true && [solarPW, pw, evChargingPower, totalHome].every(Number.isFinite);

context.set("evChargeInputs", {
    solarPW,
    pw,
    evChargingPower,
    totalHome,
    updatedAt: now.getTime()
});

// Assume totalHome includes EV charging load
const nonEvHouseLoad = telemetryReady ? totalHome - evChargingPower : null;
const availableWatts = telemetryReady ? solarPW - nonEvHouseLoad : null;
const solarAmpsRaw = telemetryReady ? availableWatts / CFG.VOLTS : null;

// ---------------------------
// Select vehicle
// ---------------------------
const tesla3PluggedIn = boolish(getState("Tesla_3_Data.plugged_in", false));
const tesla3Geofence = getState("Tesla_3_Data.geofence", null);
const teslaYPluggedIn = boolish(getState("Tesla_Y_Data.plugged_in", false));
const teslaYGeofence = getState("Tesla_Y_Data.geofence", null);

let whichTesla = null;
let vehicleID = null;

if (tesla3PluggedIn && tesla3Geofence === "Home") {
    whichTesla = "model3";
    vehicleID = 3744649900067868;
} else if (teslaYPluggedIn && teslaYGeofence === "Home") {
    whichTesla = "modely";
    vehicleID = 3744397484220282;
}

global.set("whichTesla", whichTesla);

// ---------------------------
// Vehicle battery / limits
// ---------------------------
let batteryLevel = null;
let chargeLimitSOC = null;
let teslaCapacity = null;

if (whichTesla === "model3") {
    batteryLevel = finiteOrNull(getState("Tesla_3_Data.battery_level", null));
    chargeLimitSOC = finiteOrNull(getState("Tesla_3_Data.charge_limit_soc", null));
    teslaCapacity = CFG.MODEL3_KWH;
} else if (whichTesla === "modely") {
    batteryLevel = finiteOrNull(getState("Tesla_Y_Data.battery_level", null));
    chargeLimitSOC = finiteOrNull(getState("Tesla_Y_Data.charge_limit_soc", null));
    teslaCapacity = CFG.MODELY_KWH;
}

// ---------------------------
// HA booleans / overrides
// ---------------------------
const automationDisabled = getEntityBool(
    msg,
    "tesla_charge_automation_disable",
    "input_boolean.tesla_charge_automation_disable",
    flowOverrides,
    incomingEntityId,
    incomingEntityState
);
const chargeTonight = getEntityBool(
    msg,
    "tesla_charge_tonight",
    "input_boolean.tesla_charge_tonight",
    flowOverrides,
    incomingEntityId,
    incomingEntityState
);
const chargeAsap = getEntityBool(
    msg,
    "tesla_charge_asap",
    "input_boolean.tesla_charge_asap",
    flowOverrides,
    incomingEntityId,
    incomingEntityState
);

// ---------------------------
// Persistent state
// ---------------------------
let state = context.get("evChargeState") || {
    charging: false,
    chargingSince: null,
    solarEligibleSince: null,
    recoveryActive: false,
    powerwallReserveSetForRecovery: false,
    powerwallReserveSkippedForRecovery: null,
    lastAmps: 0,
    lastMode: "idle"
};
const appliedState=flow.get('vueEvApplied'); if(appliedState){state.charging=appliedState.amps>0;state.chargingSince=appliedState.since;}
const reserveAck=flow.get('vueReserveAck'); if(reserveAck && state.powerwallReservePendingForRecovery && reserveAck.at>=Date.parse(state.powerwallReservePendingForRecovery.at)){state.powerwallReserveSetForRecovery=state.powerwallReservePendingForRecovery;}
state.recoveryActive = !!state.recoveryActive;

const teslaTelemetryAvailable =
    Number.isFinite(batteryLevel) &&
    Number.isFinite(chargeLimitSOC) &&
    Number.isFinite(teslaCapacity);

// ---------------------------
// Decision variables
// ---------------------------
let amps = 0;
let mode = "idle";
let reason = "";
let forceStop = false;
let recoveryTarget = null;
let previousMode = state.lastMode || "idle";
let solarHoldAllowed = false;
let powerwallReserveMsg = null;
let powerwallReserveRecoveryDecision = "not_recovery";

// Global automation disable yields control unless the grid-outage safety guard
// must actively stop OpenEVSE. Grid outage is absolute, including manual mode.
if (automationDisabled && !gridOutage) {
    state.lastMode = "automation_disabled";
    context.set("evChargeState", state);
    return [{vueYield:true},null];
}

// A grid outage is the highest-priority safety stop. It overrides ASAP,
// low-SOC recovery, overnight charging, solar charging, and automation disable.
if (gridOutage) {
    forceStop = true;
    reason = "Blocked because utility grid is out";
}

// Hard TOU block overrides all charging intents when the grid is available.
const touBlocked = inTouWindow(now);
if (!forceStop && touBlocked) {
    forceStop = true;
    reason = "Blocked by TOU window";
}

// ---------------------------
// Hard stop path
// ---------------------------
if (forceStop) {
    amps = 0;
    mode = "stopped";
    state.charging = false;
    state.chargingSince = null;
    state.solarEligibleSince = null;
    state.lastAmps = amps;
    state.lastMode = mode;
} else {

    // ---------------------------
    // Build requested charging intents
    // ---------------------------
    const belowRecoveryFloor = teslaTelemetryAvailable && batteryLevel < CFG.LOW_SOC;
    recoveryTarget = teslaTelemetryAvailable
        ? Math.min(CFG.RECOVERY_TARGET_SOC, chargeLimitSOC)
        : null;

    if (!whichTesla || !teslaTelemetryAvailable || batteryLevel >= chargeLimitSOC) {
        state.recoveryActive = false;
    } else if (recoveryTarget !== null && batteryLevel >= recoveryTarget) {
        state.recoveryActive = false;
    } else if (belowRecoveryFloor) {
        state.recoveryActive = true;
    }

    const overnightActive = chargeTonight && inOvernightWindow(now);

    // ASAP: command full rate immediately; the vehicle enforces its own limit.
    let asapAmps = 0;
    if (chargeAsap) {
        asapAmps = CFG.CHARGER_MAX_AMPS;
    }

    // Overnight "charge tonight": minimum amps to finish by morning
    let tonightAmps = 0;
    if (overnightActive && teslaTelemetryAvailable && batteryLevel < chargeLimitSOC) {
        tonightAmps = calcAmpsForTarget(batteryLevel, chargeLimitSOC, teslaCapacity, now);
    }

    // Low battery recovery: if the car arrived below LOW_SOC, charge at full rate
    // until RECOVERY_TARGET_SOC. TOU is handled above and does not clear this latch.
    let recoveryAmps = 0;
    if (state.recoveryActive && recoveryTarget !== null && batteryLevel < recoveryTarget) {
        recoveryAmps = CFG.CHARGER_MAX_AMPS;
    }

    // Solar logic with hysteresis
    const pwHealthyToStart = pw >= CFG.PW_START_THRESHOLD;
    const pwHealthyToContinue = pw >= CFG.PW_STOP_THRESHOLD;

    const solarCanStart =
        telemetryReady &&
        pwHealthyToStart &&
        solarAmpsRaw >= CFG.CHARGER_MIN_AMPS;

    const solarCanContinue =
        telemetryReady &&
        pwHealthyToContinue;

    // Track sustained excess solar
    if (solarCanStart) {
        if (!state.solarEligibleSince) {
            state.solarEligibleSince = now.getTime();
        }
    } else {
        state.solarEligibleSince = null;
    }

    const solarEligibleSeconds = state.solarEligibleSince
        ? (now.getTime() - state.solarEligibleSince) / 1000
        : 0;

    const solarStartConfirmed = solarEligibleSeconds >= CFG.MIN_SOLAR_START_SECONDS;

    let solarAmps = 0;
    if (solarStartConfirmed) {
        solarAmps = floorToValidAmps(solarAmpsRaw);
    }

    // ---------------------------
    // Priority order
    // ---------------------------
    // 1) ASAP
    // 2) low-SOC recovery
    // 3) charge tonight during overnight window
    // 4) solar opportunistic
    //
    // Solar charging gets a short minimum runtime to avoid relay chatter, then
    // only holds 6A when Powerwalls and solar still justify continuing.
    // ---------------------------

    if (asapAmps > 0) {
        amps = asapAmps;
        mode = "asap";
        reason = "Charge ASAP enabled";
    } else if (recoveryAmps > 0) {
        amps = recoveryAmps;
        mode = "recovery";
        reason = `Low-SOC recovery active; charge at ${CFG.CHARGER_MAX_AMPS}A until ${recoveryTarget}%`;
    } else if (tonightAmps > 0) {
        amps = tonightAmps;
        mode = "charge_tonight";
        reason = "Charge tonight schedule active";
    } else if (solarAmps > 0) {
        amps = solarAmps;
        mode = "solar";
        reason = "Excess solar available and sustained";
    } else {
        amps = 0;
        mode = "idle";
        reason = "No charging condition met";
    }

    // ---------------------------
    // Minimum run-time and anti-chatter handling
    // ---------------------------
    const wasCharging = !!state.charging;
    const runtimeSeconds = state.chargingSince
        ? (now.getTime() - state.chargingSince) / 1000
        : 0;
    const minRuntimeMet = runtimeSeconds >= CFG.MIN_CHARGE_RUN_SECONDS;
    previousMode = state.lastMode || "idle";
    solarHoldAllowed =
        solarCanContinue &&
        availableWatts > 0 &&
        ["solar", "solar_hold_min", "hold_min_runtime"].includes(previousMode);

    // If charging is currently active and no hard stop applies:
    if (wasCharging) {
        // If our freshly computed decision is 0, hold at least 6A until minimum runtime expires.
        if (amps === 0 && !minRuntimeMet && ["solar", "solar_hold_min", "hold_min_runtime"].includes(previousMode)) {
            amps = CFG.CHARGER_MIN_AMPS;
            mode = "hold_min_runtime";
            reason = `Holding minimum charge for ${CFG.MIN_CHARGE_RUN_SECONDS}s anti-chatter window`;
        }

        // After the startup anti-chatter window, only keep 6A if this was a solar
        // session and the Powerwalls/solar inputs still justify continuing.
        if (amps === 0 && minRuntimeMet && solarHoldAllowed) {
            amps = CFG.CHARGER_MIN_AMPS;
            mode = "solar_hold_min";
            reason = "Solar dipped after charging started; holding 6A while PW/solar remain healthy";
        }
    }

    // If not charging yet, do not start on weak/transient solar
    if (!wasCharging && mode === "solar" && !solarStartConfirmed) {
        amps = 0;
        mode = "idle";
        reason = "Waiting for sustained excess solar";
    }

    // If battery already at or above target/limit, stop
    if (teslaTelemetryAvailable && batteryLevel >= chargeLimitSOC) {
        amps = 0;
        mode = "complete";
        reason = "At or above charge limit";
        state.charging = false;
        state.chargingSince = null;
    } else {
        // Start/continue session tracking
        if (amps > 0) {
            if (!state.charging) {
                state.charging = true;
                state.chargingSince = now.getTime();
            }
        } else {
            state.charging = false;
            state.chargingSince = null;
        }
    }

    amps = clamp(amps, 0, CFG.CHARGER_MAX_AMPS);
    if (amps > 0 && amps < CFG.CHARGER_MIN_AMPS) amps = CFG.CHARGER_MIN_AMPS;

    state.lastAmps = amps;
    state.lastMode = mode;
}

// Raise Powerwall reserve once at the start of low-SOC recovery, but never
// during high TOU or after the pre-TOU reserve reset window has started.
if (mode !== "recovery") {
    state.powerwallReserveSetForRecovery = false;
    state.powerwallReserveSkippedForRecovery = null;
    state.powerwallReservePendingForRecovery=null;state.vueRecoveryCycle=null;
} else {
    if(!state.vueRecoveryCycle)state.vueRecoveryCycle=now.getTime();
    const reservePercentForRecovery = telemetryReady && Number.isFinite(pw)
        ? clamp(Math.ceil(pw), CFG.POWERWALL_DEFAULT_RESERVE_PERCENT, 100)
        : null;
    const raiseReserveAllowed = canRaisePowerwallReserve(now);

    if (amps === CFG.CHARGER_MAX_AMPS && reservePercentForRecovery !== null && raiseReserveAllowed && !state.powerwallReserveSetForRecovery) {
        powerwallReserveMsg = {
            topic: "powerwall_recovery_reserve",
            vueRecoveryCycle:state.vueRecoveryCycle,
            payload: {
                backup_reserve_percent: reservePercentForRecovery
            },
            url: `http://192.168.10.217:3817/api/sites/${CFG.POWERWALL_SITE_ID}/command/backup`,
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            reservePercent: reservePercentForRecovery,
            debug: {
                reason: "Low-SOC EV recovery started",
                whichTesla,
                batteryLevel,
                powerwallPercent: pw,
                reservePercent: reservePercentForRecovery,
                highTouWindow: "18:00-22:05",
                reserveResetTime: "17:55"
            }
        };
        state.powerwallReservePendingForRecovery = {
            at: now.toISOString(),
            whichTesla,
            batteryLevel,
            powerwallPercent: pw,
            reservePercent: reservePercentForRecovery
        };
        state.powerwallReserveSkippedForRecovery = null;
        powerwallReserveRecoveryDecision = `set_to_${reservePercentForRecovery}`;
    } else if (!raiseReserveAllowed) {
        state.powerwallReserveSkippedForRecovery = {
            at: now.toISOString(),
            reason: "outside allowed reserve raise window",
            highTouActive: inTouWindow(now),
            beforeReserveReset: beforePowerwallReserveReset(now),
            powerwallPercent: pw
        };
        powerwallReserveRecoveryDecision = "blocked_by_tou_guard";
    } else if (state.powerwallReserveSetForRecovery) {
        powerwallReserveRecoveryDecision = "already_set_for_recovery";
    } else {
        powerwallReserveRecoveryDecision = "not_ready";
    }
}

// Save state
context.set("evChargeState", state);

// ---------------------------
// Outputs
// ---------------------------
msg.vehicleID = vehicleID;
msg.whichTesla = whichTesla;

msg.commandArgs = msg.commandArgs || {};
msg.commandArgs.amps = amps;

if (amps > 0) {
    msg.payload = {
        state: "active",
        charge_current: amps,
        auto_release: true
    };
} else {
    msg.payload = {
        state: "disabled"
    };
}

msg.openEvseMqttTopic = "openevse/override/set";
msg.openEvseCommand = msg.payload;
msg.url = "http://192.168.30.121/override";
msg.method = "POST";
msg.headers = {
    "Content-Type": "application/json"
};

msg.debug = {
    now: now.toString(),
    incomingEntityId,
    incomingEntityState,
    whichTesla,
    vehicleID,
    teslaTelemetryAvailable,
    batteryLevel,
    chargeLimitSOC,
    teslaCapacity,
    solarPW,
    pw,
    evChargingPower,
    totalHome,
    nonEvHouseLoad,
    availableWatts,
    solarAmpsRaw,
    gridOutageRaw,
    gridOutage,
    touBlocked,
    chargeTonight,
    chargeAsap,
    automationDisabled,
    recoveryActive: state.recoveryActive,
    recoveryTarget,
    previousMode,
    solarHoldAllowed,
    powerwallReserve: {
        recoveryDecision: powerwallReserveRecoveryDecision,
        setForRecovery: state.powerwallReserveSetForRecovery || false,
        skippedForRecovery: state.powerwallReserveSkippedForRecovery || null,
        defaultReservePercent: CFG.POWERWALL_DEFAULT_RESERVE_PERCENT,
        reserveResetTime: "17:55",
        highTouWindow: "18:00-22:05"
    },
    flowOverrides: {
        automationDisabled: flowOverrides["input_boolean.tesla_charge_automation_disable"],
        chargeTonight: flowOverrides["input_boolean.tesla_charge_tonight"],
        chargeAsap: flowOverrides["input_boolean.tesla_charge_asap"]
    },
    lastMode: state.lastMode,
    charging: state.charging,
    chargingSince: state.chargingSince ? new Date(state.chargingSince).toString() : null,
    solarEligibleSince: state.solarEligibleSince ? new Date(state.solarEligibleSince).toString() : null,
    config: CFG,
    result: {
        amps,
        mode,
        reason,
        commandState: amps > 0 ? "active" : "inactive"
    }
};

return [msg, powerwallReserveMsg];

