// Node-RED Function body. Only the four family-room BR30 cans use this policy.
// Inputs are fresh cache reads made immediately before this synchronous decision.
const now = Date.now();
const date = new Date(now);
const day = [date.getFullYear(), date.getMonth() + 1, date.getDate()].join('-');
const command = msg.frCansCommand || 'tick';
const manual = ['on', 'brighter', 'off'].includes(command);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const finite = v => typeof v === 'number' && Number.isFinite(v);
const sunPosition = global.get('sunPosition') || {};
const sun = global.get('sunPercent');
const sunFresh = finite(sun) && finite(sunPosition.ts) &&
    now - sunPosition.ts >= -60000 && now - sunPosition.ts < 12 * 60000;
const s = finite(sun) ? clamp(sun, 0, 100) / 100 : 0;
function solarTime(key) {
    const ts = sunPosition.times?.[key]?.ts;
    return finite(ts) ? ts : null;
}
const solarNoon = solarTime('solarNoon');
const sunset = solarTime('sunset');
const afternoon = solarNoon !== null ? now >= solarNoon : date.getHours() >= 13;
const mode = msg.frMode;
let normal;
if (mode === 'Early Morning' || mode === 'Night') {
    normal = 1;
} else if (!afternoon) {
    normal = 1 + 49 * s * s;
} else {
    normal = 15 + 35 * s * s;
    const localFade = new Date(date);
    localFade.setHours(20, 30, 0, 0);
    // A late summer sunset moves the entire half-hour fade later.
    const fadeStart = Math.max(localFade.getTime(), sunset || 0);
    if (now >= fadeStart) normal = 15 - 14 * clamp((now - fadeStart) / 1800000, 0, 1);
}
normal = Math.round(clamp(normal, 1, 50));
if (!sunFresh && !['Early Morning', 'Night'].includes(mode)) {
    const previous = global.get('FamilyRoomCansBri');
    normal = finite(previous) ? clamp(previous, 1, 50) : 1;
}
global.set('FamilyRoomCansBri', normal);
const ctValue = global.get('CTCans');
const ct = Math.round(clamp(finite(ctValue) ? ctValue : 2500, 2500, 3500));
const state = flow.get('familyRoomCansPolicy') || {};
const actual = msg.frCansEntity?.state;
const available = actual === 'on' || actual === 'off';
const attrs = msg.frCansEntity?.attributes || {};
const recent = state.lastCommand && now - state.lastCommand.at < 10000;
const pendingOn = recent && state.lastCommand.on === true;
const isOn = available && (recent ? state.lastCommand.on : actual === 'on');
const externalHold = global.get('familyRoomCansOverride') === true;
// Adopt an existing override once; explicit OFF/TV reset still releases it.
if (state.overrideUntil === undefined) state.overrideUntil = externalHold ? now + 90 * 60000 : 0;
if (!externalHold) state.overrideUntil = 0;
if (actual === 'off' && !pendingOn) state.overrideUntil = 0;
if (state.overrideUntil <= now) state.overrideUntil = 0;
global.set('familyRoomCansOverride', state.overrideUntil > now);

// Persist the consumed date, not only a rising-edge node's volatile memory.
if (state.day !== day) {
    state.day = day;
    state.daylightBlocked = false;
    state.duskPending = false;
}
let daylightOff = false;
if (sunFresh && available && sun > 25 && state.daylightOffDate !== day) {
    state.daylightOffDate = day;
    state.daylightBlocked = true;
    state.duskPending = false;
    daylightOff = true;
}
if (sunFresh && afternoon && sun < 20 && state.daylightBlocked) {
    state.daylightBlocked = false;
    if (state.duskReleasedDate !== day) {
        state.duskReleasedDate = day;
        state.duskPending = true;
    }
}
if (mode === 'Night' || mode === 'Away') state.duskPending = false;
const blocked = state.daylightBlocked === true;
global.set('FamilyRoomCansDaylightBlocked', blocked);
const occupied = msg.frKitchenMotion === 'active' || msg.frPorchMotion === 'on' ||
    global.get('kitchenCam') === true;
const autoAllowed = ['Day', 'Early Morning', 'Evening'].includes(mode) &&
    global.get('TVStatus') === false && msg.frDisabled === 'off' &&
    msg.frChaos === 'off' && global.get('KitchenStayOn') !== true;
const wholeHouseOverride = !!global.get('BrightnessOverride');
// The dashboard color changer sets disable_main_floor_lights even when the
// separate chaos_mode toggle is off. Respect it for already-on lights too.
const repaintAllowed = msg.frChaos === 'off' && msg.frDisabled === 'off' && !wholeHouseOverride;
let target = null;
let reason = '';
function turnOn(brightness, why, transition) {
    target = { action: 'light.turn_on', target: { entity_id: ['light.family_room_can_group'] },
        data: { brightness_pct: brightness, kelvin: ct, transition } };
    reason = why;
}
function turnOff(why) {
    target = { action: 'light.turn_off', target: { entity_id: ['light.family_room_can_group'] },
        data: { transition: 2 } };
    reason = why;
}
if (command === 'off') {
    state.overrideUntil = 0;
    state.duskPending = false;
    turnOff('manual off');
} else if (manual && available) {
    const full = command === 'brighter' || isOn;
    state.overrideUntil = full ? now + 90 * 60000 : 0;
    state.duskPending = false;
    turnOn(full ? 100 : normal, full ? 'manual 100% (90 min)' : 'manual scheduled', 1);
} else if (daylightOff && available && isOn && !state.overrideUntil &&
           !wholeHouseOverride && msg.frChaos === 'off' && msg.frDisabled === 'off') {
    // This is deliberately a one-shot. A subsequent manual ON must stay on.
    turnOff('daylight >25: once per day');
} else if (available && !state.overrideUntil) {
    if (!isOn && sunFresh && !blocked && autoAllowed && !wholeHouseOverride &&
        (command === 'motion' || (state.duskPending && occupied))) {
        state.duskPending = false;
        turnOn(normal, command === 'motion' ? 'motion' : 'evening occupied', 2);
    } else if (isOn && repaintAllowed) {
        state.duskPending = false;
        // Respect off/turn-on messages still settling. Never paint over an OFF.
        if (!(recent && state.lastCommand.on === false)) turnOn(normal, 'scheduled update', 10);
    }
}
// A physical OFF observed after the request has settled cancels the hold.
if (command === 'observe' && actual === 'off' && !pendingOn) state.duskPending = false;
global.set('familyRoomCansOverride', state.overrideUntil > now);

if (target && !manual && target.action === 'light.turn_on') {
    const desired = target.data.brightness_pct;
    const current = finite(attrs.brightness) ? Math.round(attrs.brightness / 255 * 100) : null;
    const currentCt = attrs.color_temp_kelvin;
    const unchanged = actual === 'on' && current === desired &&
        finite(currentCt) && Math.abs(currentCt - ct) < 25 && attrs.color_mode === 'color_temp';
    const duplicate = recent && state.lastCommand.on === true &&
        state.lastCommand.brightness === desired && state.lastCommand.ct === ct;
    if (unchanged || duplicate) target = null;
}
if (target) {
    state.lastCommand = { at: now, on: target.action === 'light.turn_on',
        brightness: target.data.brightness_pct, ct: target.data.kelvin, reason };
    node.log('Family room cans: ' + reason + (target.data.brightness_pct ? ' -> ' + target.data.brightness_pct + '%' : ''));
}
state.lastEvaluation = { at: now, command, normal, sun, sunFresh, blocked,
    occupied, mode, actual, overrideUntil: state.overrideUntil };
flow.set('familyRoomCansPolicy', state);
node.status({ fill: !sunFresh ? 'yellow' : state.overrideUntil ? 'blue' : 'green',
    shape: 'dot', text: normal + '% scheduled; ' + (blocked ? 'daylight' : 'dark') +
        (state.overrideUntil ? '; override' : '') });
return target ? { payload: target, frCansReason: reason } : null;
