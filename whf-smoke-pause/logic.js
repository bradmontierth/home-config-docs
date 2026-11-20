// Whole House Fan Smoke Pause — decision function.
// Inputs (msg.whfspSource):
//   raw     msg.payload = one Ecowitt garage PM2.5 AQI post (~every 61 s)
//   relay   msg.topic = relay entity_id, msg.payload = 'on'/'off'
//   windows msg.payload = open window count
//   tick    60 s timer for the 2-hour give-up
// Outputs: [HA service call, Pushover, event log]
const TRIP_AQI = 50;          // pause when this many consecutive posts exceed it...
const TRIP_POSTS = 2;         // ...(~1-2 min), or the 5-post mean exceeds TRIP_AQI
const CLEAR_AQI = 25;         // resume when the 5-post mean stays <= this
const HOLD_MS = 10 * 60000;   // for this long
const FLAP_HOLD_MS = 30 * 60000;   // longer hold if we re-trip within an hour of a resume
const FLAP_WINDOW_MS = 60 * 60000;
const GIVE_UP_MS = 2 * 3600000;
const OWN_CMD_MS = 30000;     // relay 'off' echoes of our own command are not manual
const LOW = 'switch.whole_house_fan_relay_low';
const HIGH = 'switch.whole_house_fan_relay_high';

const now = Date.now();
const s = flow.get('whfSmoke') || {};
s.phase = s.phase || 'idle';
s.relays = s.relays || {};
s.window5 = s.window5 || [];
const src = msg.whfspSource;
let ha = null, push = null;
const events = [];

const fanOn = () => s.relays[LOW] === 'on' || s.relays[HIGH] === 'on';
const mean = () => s.window5.length ? s.window5.reduce((a, b) => a + b, 0) / s.window5.length : null;
const clock = t => new Date(t).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'America/Denver' });
function ev(event, extra) { events.push({ payload: Object.assign({ event, phase: s.phase, aqi: s.lastRaw, mean5: mean(), overCount: s.over, openWindows: s.windows }, extra || {}) }); }
function toIdle(event, text, extra) {
    ev(event, Object.assign({ saved: s.saved, pausedMin: s.pausedAt ? Math.round((now - s.pausedAt) / 60000) : null }, extra || {}));
    s.phase = 'idle'; s.saved = null; s.pausedAt = null; s.cleanSince = null;
    if (text) push = { payload: text };
}

if (src === 'raw') {
    const v = Number(msg.payload);
    // 500 is the sensor's error value; the existing 5-post average drops it too.
    if (Number.isFinite(v) && v >= 0 && v !== 500) {
        if (s.lastRawAt && now - s.lastRawAt > 150000) { s.over = 0; s.window5 = []; }
        s.lastRaw = v; s.lastRawAt = now;
        s.over = v > TRIP_AQI ? (s.over || 0) + 1 : 0;
        s.window5.push(v); if (s.window5.length > 5) s.window5.shift();
        const m = mean();
        if (s.phase === 'idle' && fanOn() && (s.over >= TRIP_POSTS || (s.window5.length === 5 && m > TRIP_AQI))) {
            s.phase = 'paused';
            s.saved = { low: s.relays[LOW] === 'on', high: s.relays[HIGH] === 'on' };
            s.pausedAt = now; s.cleanSince = null; s.ownCmdAt = now;
            s.holdMs = s.lastResumeAt && now - s.lastResumeAt < FLAP_WINDOW_MS ? FLAP_HOLD_MS : HOLD_MS;
            ha = { payload: { action: 'switch.turn_off', target: { entity_id: [LOW, HIGH] } } };
            const speed = s.saved.high ? 'high' : 'low';
            push = { payload: `Outdoor AQI jumped to ${v} (5-min avg ${Math.round(m)}). Paused the fan (was ${speed}). It resumes after the avg stays ≤${CLEAR_AQI} for ${s.holdMs / 60000} min with a window open.`, priority: 0 };
            ev('paused', { saved: s.saved, holdMin: s.holdMs / 60000 });
        } else if (s.phase === 'paused') {
            if (s.window5.length === 5 && m <= CLEAR_AQI) {
                s.cleanSince = s.cleanSince || now;
                if (now - s.cleanSince >= (s.holdMs || HOLD_MS)) {
                    if (!(s.windows >= 1)) {
                        toIdle('not_resumed_windows_closed', `Air cleared, but all windows are closed. Leaving the fan off.`);
                    } else {
                        const ids = [];
                        if (s.saved && s.saved.low) ids.push(LOW);
                        if (s.saved && s.saved.high) ids.push(HIGH);
                        if (!ids.length) ids.push(LOW);
                        ha = { payload: { action: 'switch.turn_on', target: { entity_id: ids } } };
                        s.ownCmdAt = now;
                        s.lastResumeAt = now;
                        toIdle('resumed', `Outdoor air is clean again (5-min avg ${Math.round(m)}). Resumed the fan on ${s.saved && s.saved.high ? 'high' : 'low'} (paused since ${clock(s.pausedAt)}).`, { resumed: ids });
                    }
                }
            } else {
                s.cleanSince = null;
            }
        }
    }
} else if (src === 'relay') {
    const id = msg.topic, st = String(msg.payload);
    if (id === LOW || id === HIGH) {
        s.relays[id] = st;
        const own = s.ownCmdAt && now - s.ownCmdAt < OWN_CMD_MS;
        if (s.phase === 'paused' && st === 'on' && !own) {
            toIdle('cancelled_manual_on', `Fan was turned on by hand during the smoke pause. Smoke pause cancelled.`, { relay: id });
        }
    }
} else if (src === 'windows') {
    const n = Number(msg.payload);
    if (Number.isFinite(n)) {
        s.windows = n;
        if (s.phase === 'paused' && n === 0) toIdle('cancelled_windows_closed', `All windows closed during the smoke pause. The fan won't turn back on.`);
    }
} else if (src === 'tick') {
    if (s.phase === 'paused' && now - s.pausedAt > GIVE_UP_MS) {
        toIdle('gave_up', `Outdoor air still isn't clean after 2 hours (AQI ${s.lastRaw}, 5-min avg ${Math.round(mean())}). Leaving the fan off.`);
    }
}

flow.set('whfSmoke', s);
const m = mean();
node.status(s.phase === 'paused'
    ? { fill: 'red', shape: 'dot', text: `paused ${clock(s.pausedAt)} · aqi ${s.lastRaw} avg ${m === null ? '?' : Math.round(m)}${s.cleanSince ? ' · clean ' + Math.round((now - s.cleanSince) / 60000) + 'm' : ''}` }
    : { fill: fanOn() ? 'green' : 'grey', shape: 'dot', text: `idle · fan ${fanOn() ? 'on' : 'off'} · aqi ${s.lastRaw ?? '?'} avg ${m === null ? '?' : Math.round(m)} · win ${s.windows ?? '?'}` });
return [ha, push, events.length ? events : null];
