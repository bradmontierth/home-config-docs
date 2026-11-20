// Offline tests against the prepared (or re-read deployed) tab. No HA/MQTT calls.
// node test_logic.js /path/to/tab.json
const fs = require('fs');
const assert = require('assert/strict');
const tab = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const source = name => tab.nodes.find(n => n.name === name).func;
let now = 1800000000000;
let hour = 12;
class Clock extends Date {
    getHours() { return hour; }
    static now() { return now; }
}
const context = values => ({get: k => values[k], set: (k,v) => values[k] = v});
const hot = () => ({delta:-1, expiresAt:now+3600000});
const cold = () => ({delta:1, expiresAt:now+3600000});
const mega = new Function('msg','global','flow','Date',source('Combined Mega Logic'));
let count = 0;
function run(opts={}) {
    const values = {
        ecobeeWeatherStationIndoorTemp:opts.temp ?? 78.6,
        ecobeeOfficeMotionTemp:opts.office ?? 71.276,
        currentEcobeeState:{hvacHeatCool:opts.hvac ?? 'cool',hvacState:'idle'},
        windowsExclBasement:opts.windows ?? 'closed',
        ecobeeWeatherCoolActive:false,
        ecobeeWeatherStationCoolingDemand:opts.active ?? false,
        ecobeeForcedCoolActualStartedAt:opts.started ?? 0,
        thermostatoverride:opts.manual ?? false,
        heatHomeSP:opts.heatSP ?? 67,
    };
    const state = {mode:opts.mode ?? 'Day',mainComfortHold:opts.hold};
    const msg = mega({gridOutage:opts.outage ?? 'off'},context(values),context(state),Clock);
    return {p:msg.payload, msg, values, state};
}
function check(name, opts, forced, target) {
    const {p} = run(opts);
    assert.equal(p.sensorCooling.forcedCoolActive,forced,name);
    if(target !== undefined) assert.equal(p.temperatureSetPoint,target,name);
    count++;
}
check('normal 78.6 idle',{},false,80);
check('normal starts at 79',{temp:79},true,65);
check('hot starts now',{hold:hot()},true,65);
check('hot boundary 78',{hold:hot(),temp:78},true);
check('hot below boundary',{hold:hot(),temp:77.9},false);
check('hot holds until 76',{hold:hot(),temp:76.1,active:true},true);
check('hot stop at 76',{hold:hot(),temp:76,active:true},false);
check('cold waits for 80',{hold:cold(),temp:79.9},false);
check('cold start at 80',{hold:cold(),temp:80},true);
check('cold stop at 78',{hold:cold(),temp:78,active:true},false);
check('office demand retained',{hold:cold(),office:75},true);
check('minimum runtime retained',{hold:cold(),temp:77,started:now-600000},true);
check('finished minimum runtime',{hold:cold(),temp:77,started:now-1800000},false);
check('expired cooling hold',{hold:{delta:-1,expiresAt:now}},false,80);
assert.equal(run({hold:{delta:-1,expiresAt:now}}).state.mainComfortHold,null);
check('invalid delta',{hold:{delta:-8,expiresAt:now+3000}},false,80);
check('invalid expiry',{hold:{delta:-1,expiresAt:'tomorrow'}},false,80);
check('excessive hold duration',{hold:{delta:-1,expiresAt:now+7200000}},false,80);
check('outage blocks hot',{hold:hot(),outage:'on'},false,85);
check('windows block hot',{hold:hot(),windows:'open'},false,85);
check('away blocks hot',{hold:hot(),mode:'Away'},false,85);
check('night blocks hot',{hold:hot(),mode:'Night'},false,85);
check('manual override disables voice offset',{hold:hot(),manual:true},false,80);
check('heat normal',{hvac:'heat'},false,67);
check('heat hot lowers target',{hvac:'heat',hold:hot()},false,66);
check('heat cold raises target',{hvac:'heat',hold:cold()},false,68);
check('heat uses current configured target',{hvac:'heat',hold:cold(),heatSP:69},false,70);
check('heat expires to current target',{hvac:'heat',hold:{delta:1,expiresAt:now},heatSP:69},false,69);
for (const opts of [{windows:'open'},{mode:'Away'},{mode:'Night'}]) {
    check('heat scheduled restriction', {...opts,hvac:'heat',hold:cold()},false,59);
}
check('heat voice yields to outage',{hvac:'heat',hold:cold(),outage:'on'},false,67);
check('heat voice yields to manual override',{hvac:'heat',hold:cold(),manual:true},false,67);
check('off stays outside heat/cool branch',{hvac:'off',hold:cold()},false);
assert.equal(run({hvac:'off',hold:cold()}).p.comfortHold.appliedDelta,0);
hour=18;
check('clock night blocks hot',{hold:hot()},false,85);
check('clock night heat baseline',{hvac:'heat',hold:cold()},false,59);
hour=17;
check('pre-cooling preserved',{hold:cold()},false,73);
hour=12;

// Downstream manual-override gate stays authoritative, including outage priority.
const gate = new Function('msg','global',source('temp set point'));
assert.equal(gate(run({hold:hot()}).msg,context({thermostatoverride:true})),null);
count++;

// Timer/refresh/recovery tested with a deterministic fake clock and scheduler.
const holdFn = new Function('msg','flow','node','Date','setTimeout','clearTimeout',
    source('Main house 60 minute voice hold'));
const state = {};
let nextId=0;
const timers = new Map();
const sent=[];
const node = {status:()=>{},send:msg=>sent.push(msg)};
const schedule = (fn,delay) => {const id=++nextId;timers.set(id,{fn,due:now+delay});return id;};
const clear = id => timers.delete(id);
const send = (topic,payload='PRESS',retain=false) => holdFn({topic,payload,retain},context(state),node,Clock,schedule,clear);
assert.equal(send('voice/button/not_a_command'),null);
assert.equal(send('voice/button/main_comfort_hot','PRESS',true),null);
assert.equal(send('voice/button/main_comfort_hot','bad'),null);
assert.equal(state.mainComfortHold,undefined);
send('voice/button/main_comfort_hot');
assert.deepEqual(state.mainComfortHold,hot());
const oldCallback=[...timers.values()][0].fn;
now+=600000;
send('voice/button/main_comfort_hot');
assert.deepEqual(state.mainComfortHold,hot());
assert.equal(timers.size,1);
now+=600000;
send('voice/button/main_comfort_cold');
assert.deepEqual(state.mainComfortHold,cold());
oldCallback();
assert.deepEqual(state.mainComfortHold,cold());
// Mimic a deploy: Node-RED clears old timers, flow timestamp persists.
timers.clear(); node.mainComfortTimer=null;
now+=600000;
const saved={...state.mainComfortHold};
send('main_comfort/recover');
assert.deepEqual(state.mainComfortHold,saved);
assert.equal(timers.size,1);
const timer=[...timers.values()][0];
assert.equal(timer.due,saved.expiresAt);
now=timer.due;
timer.fn();
assert.equal(state.mainComfortHold,null);
assert.equal(sent.length,1);
state.mainComfortHold={delta:1,expiresAt:now-1};
send('main_comfort/recover');
assert.equal(state.mainComfortHold,null);
count+=10;

// Compile every function and validate all newly added wires locally.
const ids=new Set(tab.nodes.map(n=>n.id));
for (const n of tab.nodes) {
    if(n.type==='function') new Function('msg','node','flow','global',n.func);
    if(n.id.startsWith('ec_comfort_')) for(const wire of n.wires.flat()) assert(ids.has(wire),wire);
}
console.log(`PASS: ${count} cooling, heating, protection, expiry and recovery checks; functions compile.`);
