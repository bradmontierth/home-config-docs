const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const code = fs.readFileSync(__dirname + '/controller.js', 'utf8');
let checks = 0;
function test(name, body) {
    body(); checks++; console.log('PASS ' + name);
}
function harness() {
    let now = Date.parse('2026-09-10T10:00:00-06:00');
    const g = {sunPercent: 10, CTCans: 2800, TVStatus: false, kitchenCam: false,
        BriCurveCans: 73, BriCurve50: 80, familyRoomCansOverride: false};
    const f = {};
    const input = {frMode: 'Day', frKitchenMotion: 'inactive', frPorchMotion: 'off',
        frDisabled: 'off', frChaos: 'off', frCansEntity: {state: 'off', attributes: {}}};
    function time(iso, sun) {
        now = Date.parse(iso);
        if (sun !== undefined) g.sunPercent = sun;
        const d = iso.slice(0, 10);
        g.sunPosition = {ts: now, times: {solarNoon: {ts: Date.parse(d + 'T13:25:00-06:00')},
            sunset: {ts: Date.parse(d + 'T19:47:00-06:00')}}};
    }
    time('2026-09-10T10:00:00-06:00', 10);
    function run(cmd = 'tick', apply = true) {
        class FakeDate extends Date {
            constructor(...args) { super(...(args.length ? args : [now])); }
            static now() { return now; }
        }
        const ctx = {Date: FakeDate, msg: {...input, frCansCommand: cmd},
            global: {get: k => g[k], set: (k,v) => {g[k] = v;}},
            flow: {get: k => f[k], set: (k,v) => {f[k] = v;}},
            node: {log() {}, status() {}}};
        const out = vm.runInNewContext('(function(){' + code + '\n})()', ctx);
        if (out && apply) {
            const on = out.payload.action === 'light.turn_on';
            input.frCansEntity = {state: on ? 'on' : 'off', attributes: on ? {
                brightness: Math.round(out.payload.data.brightness_pct / 100 * 255),
                color_temp_kelvin: out.payload.data.kelvin, color_mode: 'color_temp'} : {}};
        }
        return out;
    }
    return {g, f, input, time, run};
}
test('quadratic morning ramp and family-room-only globals', () => {
    const h = harness();
    for (const [sun, expected] of [[0,1],[25,4],[50,13],[75,29],[100,50]]) {
        h.time('2026-09-10T11:00:00-06:00', sun); h.run();
        assert.equal(h.g.FamilyRoomCansBri, expected);
    }
    assert.equal(h.g.BriCurveCans, 73); assert.equal(h.g.BriCurve50, 80);
});
test('early morning and night stay at 1%', () => {
    const h = harness();
    for (const mode of ['Early Morning', 'Night']) {
        h.input.frMode = mode; h.g.sunPercent = 90; h.run();
        assert.equal(h.g.FamilyRoomCansBri, 1);
    }
});
test('afternoon 50→15; evening holds 15 then fades to 1', () => {
    const h = harness();
    for (const [time,sun,expected] of [['14:00:00',100,50],['17:00:00',50,24],
        ['19:47:00',0,15],['20:29:00',0,15],['20:45:00',0,8],['21:00:00',0,1]]) {
        h.time('2026-09-10T'+time+'-06:00',sun);h.run();assert.equal(h.g.FamilyRoomCansBri,expected);
    }
});
test('late sunset shifts the fade by 30 minutes', () => {
    const h = harness(); h.time('2026-07-10T20:45:00-06:00', 0);
    h.g.sunPosition.times.sunset.ts = Date.parse('2026-07-10T20:55:00-06:00');
    h.run();assert.equal(h.g.FamilyRoomCansBri,15);
    h.time('2026-07-10T21:10:00-06:00',0);
    h.g.sunPosition.times.sunset.ts = Date.parse('2026-07-10T20:55:00-06:00');
    h.run();assert.equal(h.g.FamilyRoomCansBri,8);
});
test('daylight OFF once despite occupied room; daytime motion stays off', () => {
    const h = harness(); h.input.frKitchenMotion = 'active'; h.run('on');
    h.time('2026-09-10T10:05:00-06:00',26);
    assert.equal(h.run().payload.action,'light.turn_off');
    for(let i=6;i<15;i++) {h.time(`2026-09-10T10:${String(i).padStart(2,'0')}:00-06:00`,30);assert.equal(h.run('motion'),null);}
    assert.equal(h.f.familyRoomCansPolicy.daylightOffDate,'2026-9-10');
});
test('manual scheduled ON survives daylight ticks and second ON gets 100%', () => {
    const h = harness(); h.time('2026-09-10T11:00:00-06:00',50);h.run();
    assert.equal(h.run('on').payload.data.brightness_pct,13);
    h.time('2026-09-10T11:01:00-06:00',50);assert.equal(h.run(),null);
    assert.equal(h.run('on').payload.data.brightness_pct,100);
    h.time('2026-09-10T11:02:00-06:00',50);assert.equal(h.run('motion'),null);
    assert.equal(h.g.familyRoomCansOverride,true);
});
test('manual press concurrent with threshold consumes OFF without fighting user', () => {
    const h = harness();h.time('2026-09-10T11:00:00-06:00',50);
    assert.equal(h.run('on').payload.data.brightness_pct,13);
    h.time('2026-09-10T11:01:00-06:00',50);assert.equal(h.run(),null);
});
test('90-minute override persists across function recreation and expires to curve', () => {
    const h = harness();h.time('2026-09-10T11:00:00-06:00',50);h.run('brighter');
    h.time('2026-09-10T12:29:00-06:00',50);assert.equal(h.run(),null);
    h.time('2026-09-10T12:30:00-06:00',50);assert.equal(h.run().payload.data.brightness_pct,13);
    assert.equal(h.g.familyRoomCansOverride,false);
});
test('OFF releases override and next ON is scheduled', () => {
    const h = harness();h.run('brighter');h.run('off');
    assert.equal(h.g.familyRoomCansOverride,false);
    assert.equal(h.run('on').payload.data.brightness_pct,1);
});
test('rapid presses use pending commands while HA state catches up', () => {
    const h = harness();assert.equal(h.run('on',false).payload.data.brightness_pct,1);
    assert.equal(h.run('on',false).payload.data.brightness_pct,100);
    h.run('off',false);assert.equal(h.run('on',false).payload.data.brightness_pct,1);
});
test('occupied evening crossing turns on even with KitchenMotionLighting latched', () => {
    const h = harness(); h.g.KitchenMotionLighting='on';h.time('2026-09-10T12:00:00-06:00',75);h.run();
    h.time('2026-09-10T18:00:00-06:00',21);h.input.frKitchenMotion='active';assert.equal(h.run(),null);
    h.time('2026-09-10T18:05:00-06:00',19);
    assert.equal(h.run().payload.data.brightness_pct,16);
    h.run('off');h.time('2026-09-10T18:06:00-06:00',18);assert.equal(h.run(),null);
});
test('unoccupied evening waits for occupancy, without new sensor edge requirement', () => {
    const h = harness();h.time('2026-09-10T12:00:00-06:00',75);h.run();
    h.time('2026-09-10T18:05:00-06:00',19);assert.equal(h.run(),null);
    h.time('2026-09-10T18:06:00-06:00',18);h.input.frPorchMotion='on';assert.ok(h.run());
});
test('daylight marker survives reload; next day gets one new OFF', () => {
    const h = harness();h.time('2026-09-10T11:00:00-06:00',50);h.run();h.run('on');
    h.time('2026-09-10T11:01:00-06:00',50);
    h.f.familyRoomCansPolicy=JSON.parse(JSON.stringify(h.f.familyRoomCansPolicy));
    assert.equal(h.run(),null);
    h.time('2026-09-11T11:00:00-06:00',50);assert.equal(h.run().payload.action,'light.turn_off');
});
test('invalid/stale solar data never triggers daylight OFF or automatic ON', () => {
    const h = harness();h.run('on');h.time('2026-09-10T11:00:00-06:00',80);h.g.sunPosition.ts-=13*60000;
    assert.equal(h.run(),null);assert.equal(h.f.familyRoomCansPolicy.daylightOffDate,undefined);
    h.run('off');assert.equal(h.run('motion'),null);
    h.g.sunPercent=NaN;assert.equal(h.run('motion'),null);
});
test('TV, disabled, chaos, night, away and stay-on gates prevent auto ON', () => {
    for (const variant of ['TV','disabled','chaos','Night','Away','stay']) {
        const h=harness();
        if(variant==='TV')h.g.TVStatus=true;
        if(variant==='disabled')h.input.frDisabled='on';
        if(variant==='chaos')h.input.frChaos='on';
        if(variant==='Night'||variant==='Away')h.input.frMode=variant;
        if(variant==='stay')h.g.KitchenStayOn=true;
        assert.equal(h.run('motion'),null,variant);
        assert.ok(h.run('on'), 'manual bypass '+variant);
    }
});
test('unavailable group does not consume daylight OFF or issue automatic commands', () => {
    const h=harness();h.time('2026-09-10T11:00:00-06:00',80);h.input.frCansEntity.state='unavailable';
    assert.equal(h.run(),null);assert.equal(h.f.familyRoomCansPolicy.daylightOffDate,undefined);
    h.input.frCansEntity.state='on';assert.equal(h.run().payload.action,'light.turn_off');
});


test('color changer disable gate preserves RGB on tick, repaint, motion and observe', () => {
    const h = harness();
    h.input.frDisabled = 'on';
    h.input.frChaos = 'off';
    h.input.frCansEntity = {state:'on', attributes:{color_mode:'hs',brightness:255}};
    for (const cmd of ['tick','repaint','motion','observe']) assert.equal(h.run(cmd),null);
    h.input.frDisabled = 'off';
    assert.equal(h.run('repaint').payload.data.kelvin,2800);
});
test('manual control still works while automatic repaint is disabled', () => {
    const h = harness(); h.input.frDisabled = 'on';
    assert.equal(h.run('on').payload.action,'light.turn_on');
    assert.equal(h.run('off').payload.action,'light.turn_off');
});
console.log(`${checks} scenario tests passed`);
