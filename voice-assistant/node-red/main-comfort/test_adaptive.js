// No live services: execute the prepared Node-RED functions with mock context.
const fs=require('fs'), assert=require('assert/strict');
const tab=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const src=id=>tab.nodes.find(n=>n.id===id).func;
let now=1800000000000, hour=12;
class Clock extends Date {static now(){return now;} getHours(){return hour;}}
const ctx=o=>({get:k=>o[k],set:(k,v)=>o[k]=v});
let seq=0, timers=new Map(), sent=[];
const node={status:()=>{},send:m=>sent.push(m)};
const setTimer=(fn,delay)=>{const id=++seq;timers.set(id,{fn,at:now+delay});return id;};
const clearTimer=id=>timers.delete(id);
const fn=new Function('msg','global','flow','node','Date','setTimeout','clearTimeout',src('ec_comfort_hold'));
const mega=new Function('msg','global','flow','Date',src('11ea46a27b5ca06a'));
let flow={}, globals={}, input={};
function reset(opts={}) {
    flow={mode:opts.mode??'Day'};
    globals={heatHomeSP:opts.baseline??67,windowsExclBasement:opts.windows??'closed',thermostatoverride:opts.manual??false,
        ecobeeWeatherStationIndoorTemp:opts.temp??76.8,ecobeeOfficeMotionTemp:71,
        currentEcobeeState:{hvacHeatCool:opts.hvac??'cool',hvacState:'idle',currentTemperature:opts.temp??76.8}};
    input={gridOutage:opts.grid??'off',comfortClimate:{state:opts.hvac??'cool',attributes:{current_temperature:opts.temp??76.8}},comfortWeather:{state:String(opts.temp??76.8)}};
    timers=new Map(); sent=[];node.mainComfortTimer=null;
}
function call(extra){return fn({...input,...extra},ctx(globals),ctx(flow),node,Clock,setTimer,clearTimer);}
function plan(kind='hot'){return call({req:{query:{kind,request_id:'request_'+(++seq)}}})[1].payload;}
function apply(p,confirmed=false){call({topic:'voice/main_comfort/apply',payload:JSON.stringify({proposal:p,confirmed})});return flow.mainComfortResults?.find(r=>r.id===p.requestId)?.result;}
function evaluate(){return mega({gridOutage:input.gridOutage},ctx(globals),ctx(flow),Clock).payload;}
let checks=0;
function check(label,cb){reset();cb();checks++;}
check('77.5 automatic, one-degree band',()=>{reset({temp:77.5});const x=plan();assert.equal(x.requires_confirmation,false);assert.equal(x.proposal.startAt,77.5);assert.equal(x.proposal.stopAt,76.5);assert(apply(x.proposal).ok);assert(evaluate().sensorCooling.forcedCoolActive);});
check('76.8 waits for explicit yes',()=>{const p=plan();assert.equal(p.adjustment,2.2);assert.equal(p.requires_confirmation,true);assert.equal(p.proposal.stopAt,75.8);assert.equal(flow.mainComfortHold,undefined);assert.equal(apply(p.proposal).ok,false);assert.equal(flow.mainComfortHold,undefined);});
check('confirmed 76.8 acts immediately',()=>{const p=plan().proposal;assert(apply(p,true).ok);const out=evaluate();assert.equal(out.comfortHold.weatherStartAt,76.8);assert.equal(out.comfortHold.weatherStopAt,75.8);assert.equal(out.temperatureSetPoint,65);});
check('2 degrees allowed automatically',()=>{reset({temp:77});assert.equal(plan().requires_confirmation,false);});
check('4 degrees allowed with yes',()=>{reset({temp:75});const p=plan();assert(p.ok&&p.requires_confirmation);assert(apply(p.proposal,true).ok);});
check('beyond 4 rejected without disturbing old hold',()=>{const p=plan().proposal;apply(p,true);const old=flow.mainComfortHold;input.comfortWeather.state='74.9';assert.equal(plan().ok,false);assert.equal(flow.mainComfortHold,old);});
check('floor avoids trigger just above measured value',()=>{reset({temp:77.55});assert.equal(plan().proposal.startAt,77.5);});
check('cooling cold request releases demand',()=>{reset({temp:76.8});const p=plan('cold');assert.equal(p.proposal.startAt,80);assert.equal(p.proposal.stopAt,79);apply(p.proposal);globals.ecobeeWeatherStationCoolingDemand=true;assert.equal(evaluate().sensorCooling.forcedCoolActive,false);});
check('cooling cold adaptive limit',()=>{reset({temp:81});const p=plan('cold');assert.equal(p.adjustment,3);assert(p.requires_confirmation);assert.equal(p.proposal.stopAt,81);});
check('heat cold automatic',()=>{reset({hvac:'heat',temp:68});const p=plan('cold');assert.equal(p.proposal.heatTarget,69);assert.equal(p.requires_confirmation,false);apply(p.proposal);assert.equal(evaluate().temperatureSetPoint,69);});
check('heat cold confirmation',()=>{reset({hvac:'heat',temp:69});const p=plan('cold');assert.equal(p.proposal.heatTarget,70);assert(p.requires_confirmation);apply(p.proposal,true);assert.equal(evaluate().temperatureSetPoint,70);});
check('heat hot confirmation',()=>{reset({hvac:'heat',temp:65});const p=plan('hot');assert.equal(p.proposal.heatTarget,64);assert(p.requires_confirmation);apply(p.proposal,true);assert.equal(evaluate().temperatureSetPoint,64);});
check('heat never exceeds four',()=>{reset({hvac:'heat',temp:72});assert.equal(plan('cold').ok,false);});
check('frozen after room cools',()=>{const p=plan().proposal;apply(p,true);globals.ecobeeWeatherStationIndoorTemp=76.1;globals.ecobeeWeatherStationCoolingDemand=true;assert.equal(evaluate().comfortHold.weatherStartAt,76.8);assert(evaluate().sensorCooling.forcedCoolActive);globals.ecobeeWeatherStationIndoorTemp=75.8;assert.equal(evaluate().sensorCooling.forcedCoolActive,false);});
check('minimum runtime still wins',()=>{apply(plan().proposal,true);globals.ecobeeWeatherStationIndoorTemp=75;globals.ecobeeForcedCoolActualStartedAt=now-1000;assert(evaluate().sensorCooling.forcedCoolActive);});
check('office demand unchanged',()=>{apply(plan('cold').proposal);globals.ecobeeOfficeMotionTemp=75;assert(evaluate().sensorCooling.forcedCoolActive);});
for(const [label,opts] of [['windows',{windows:'open'}],['night',{mode:'Night'}],['away',{mode:'Away'}],['outage',{grid:'on'}],['unknown grid',{grid:'unknown'}],['manual',{manual:true}],['off',{hvac:'off'}],['auto',{hvac:'heat_cool'}]]) {
    check(label+' blocks proposal',()=>{reset(opts);assert.equal(plan().ok,false);});
}
check('night clock blocks',()=>{hour=18;assert.equal(plan().ok,false);hour=12;});
check('invalid sensor blocks',()=>{input.comfortWeather.state='unavailable';assert.equal(plan().ok,false);});
check('mode change between question and yes rejects',()=>{const p=plan().proposal;input.comfortClimate.state='heat';assert.equal(apply(p,true).ok,false);});
check('baseline change between heat question and yes rejects',()=>{reset({hvac:'heat',temp:69});const p=plan('cold').proposal;globals.heatHomeSP=68;assert.equal(apply(p,true).ok,false);});
check('mode changed during hold does not reinterpret it',()=>{apply(plan().proposal,true);globals.currentEcobeeState.hvacHeatCool='heat';assert.equal(evaluate().temperatureSetPoint,67);});
check('expired confirmation rejects',()=>{const p=plan().proposal;now+=90001;assert.equal(apply(p,true).ok,false);});
check('retained command ignored',()=>{const p=plan().proposal;call({topic:'voice/main_comfort/apply',retain:true,payload:{proposal:p,confirmed:true}});assert.equal(flow.mainComfortHold,undefined);});
check('MQTT duplicate does not extend hold',()=>{const p=plan().proposal;apply(p,true);const ends=flow.mainComfortHold.expiresAt;now+=1000;apply(p,true);assert.equal(flow.mainComfortHold.expiresAt,ends);});
check('repeat replans against normal baseline, no stacking',()=>{apply(plan().proposal,true);now+=1000;const p=plan().proposal;apply(p,true);assert.equal(flow.mainComfortHold.startAt,76.8);assert.equal(flow.mainComfortHold.expiresAt,now+3600000);assert.equal(timers.size,1);});
check('opposite request replaces',()=>{apply(plan().proposal,true);apply(plan('cold').proposal);assert.equal(flow.mainComfortHold.startAt,80);});
check('old buttons cannot bypass confirmation',()=>{call({topic:'voice/button/main_comfort_hot',payload:'PRESS'});assert.equal(flow.mainComfortHold,undefined);});
check('old buttons support auto tier',()=>{reset({temp:77.5});call({topic:'voice/button/main_comfort_hot',payload:'PRESS'});assert.equal(flow.mainComfortHold.startAt,77.5);});
check('expiry restores baseline and reevaluates',()=>{apply(plan().proposal,true);now=flow.mainComfortHold.expiresAt;[...timers.values()][0].fn();assert.equal(flow.mainComfortHold,null);assert.equal(sent.length,1);assert.equal(evaluate().comfortHold.weatherStartAt,79);});
check('recovery preserves remaining hour',()=>{apply(plan().proposal,true);const ends=flow.mainComfortHold.expiresAt;timers.clear();node.mainComfortTimer=null;now+=600000;call({topic:'main_comfort/recover'});assert.equal([...timers.values()][0].at,ends);});
check('legacy hold survives rollout until expiry',()=>{flow.mainComfortHold={delta:-1,expiresAt:now+600000};call({topic:'main_comfort/recover'});assert.equal(evaluate().comfortHold.weatherStartAt,78);assert.equal(evaluate().comfortHold.weatherStopAt,76);});
check('invalid four-plus proposal refused by writer and Mega Logic',()=>{const p={...plan().proposal,startAt:74,stopAt:73};assert.equal(apply(p,true).ok,false);flow.mainComfortHold={...p,expiresAt:now+600000};assert.equal(evaluate().comfortHold.active,false);});
check('malformed band rejected',()=>{const p={...plan().proposal,stopAt:70};assert.equal(apply(p,true).ok,false);});
for(const n of tab.nodes) if(n.type==='function') new Function('msg','global','flow','node',n.func);
console.log(`PASS: ${checks} adaptive policy, confirmation-bound, heating/cooling and timer scenarios; functions compile.`);
