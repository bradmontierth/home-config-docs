'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {valid,PowerCache,Appliance,OutageGuard,Window,DishwasherFeatures,CommandGate,evPowerTuple,evIntent}=require('./logic.cjs');
const sample=(watts,t)=>({watts,reportedAt:t,available:true});
function run(kind,interval,profile,end){const engine=new Appliance(kind),events=[];let s;
 for(let t=0;t<=end;t+=1000){if(t%interval===0)s=sample(profile(t),t);events.push(...engine.evaluate(s,t));}return events;}
for(const interval of [1000,5000,10000]){
 test(`dryer real dwell independent of ${interval} ms reports`,()=>{
  const e=run('dryer',interval,t=>t<120000?800:0,180000).filter(x=>x.key==='status');
  assert.deepEqual(e.map(x=>[x.value,x.at]),[['running',60000],['complete',155000]]);
 });
 test(`dishwasher equivalent legacy timing at ${interval} ms`,()=>{
  const e=run('dishwasher',interval,t=>t<720000?800:0,910000).filter(x=>x.key==='status');
  assert.deepEqual(e.map(x=>[x.value,x.at]),[['running',30000],['clean',900000]]);
 });
 test(`outage guard uses 10 seconds, not two samples, at ${interval} ms`,()=>{
  const g=new OutageGuard();let s,out=[];
  for(let t=0;t<=30000;t+=1000){if(t%interval===0)s=sample(500,t);out.push(...g.evaluate(s,t,true));}
  assert.deepEqual(out.map(x=>x.at),[10000]);
 });
}
test('range catches a five-second pulse; one indicator on/off',()=>{
 const e=run('range',1000,t=>t<5000?1300:0,66000).filter(x=>x.key==='indicator');
 assert.deepEqual(e.map(x=>[x.value,x.at]),[[true,0],[false,65000]]);
});
test('oven latch needs 180 seconds; survives short cycling and releases after five minutes',()=>{
 const e=run('range',1000,t=>t<200000?1300:0,510000);
 assert.deepEqual(e.filter(x=>x.key==='ovenOn').map(x=>[x.value,x.at]),[[true,180000],[false,500000]]);
 assert.equal(e.filter(x=>x.key==='indicator'&&x.value===false)[0].at,500000);
});
test('short dishwasher wake never creates clean',()=>{
 const e=run('dishwasher',1000,t=>t<60000?50:0,300000);
 assert(!e.some(x=>x.value==='clean'));assert(e.some(x=>x.value==='dirty'));
});
test('missing watts, strings, future times and unavailable are never zero',()=>{
 for(const w of [null,undefined,'0','unavailable',NaN,Infinity,-1])assert.equal(valid(sample(w,0),0),false);
 assert.equal(valid(sample(1,1000),0),false);assert.equal(valid({...sample(0,0),available:false},0),false);
});
test('out-of-order cache update rejected; explicit unavailability accepted',()=>{
 const c=new PowerCache();assert(c.put('x',sample(500,10),10));assert(!c.put('x',sample(0,9),10));
 assert.equal(c.get('x').watts,500);assert(c.put('x',{...sample(500,10),available:false},11));assert(!valid(c.get('x'),11));
});
test('silence cannot finish dryer or bridge a start qualification',()=>{
 const a=new Appliance('dryer');for(let t=0;t<30000;t+=1000)a.evaluate(sample(500,t),t);
 for(let t=30000;t<90000;t+=1000)assert(!a.evaluate(sample(500,29000),t).some(x=>x.value==='running'));
 for(let t=90000;t<150000;t+=1000)assert(!a.evaluate(sample(500,t),t).some(x=>x.value==='running'));
 assert(a.evaluate(sample(500,150000),150000).some(x=>x.value==='running'));
 assert(!a.evaluate({available:false,reportedAt:151000},151000).some(x=>x.value==='complete'));
});
test('gap in low observations restarts completion dwell',()=>{
 const a=new Appliance('dryer',{status:'running'});a.evaluate(sample(0,0),0);a.evaluate(sample(0,10000),10000);
 assert(!a.evaluate(sample(0,100000),100000).some(x=>x.value==='complete'));
});
test('unchanged power plus fresh reports qualifies on clock deadlines',()=>{
 const a=new Appliance('dryer');let out=[];for(let t=0;t<=60000;t+=1000)out.push(...a.evaluate(sample(80,Math.floor(t/10000)*10000),t));
 assert(out.some(x=>x.value==='running'&&x.at===60000));
});
test('no startup dryer-complete alert from an idle snapshot',()=>{
 assert(!run('dryer',1000,()=>0,120000).some(x=>x.value==='complete'));
});
test('outage alert rearms only after genuine low interval or grid restoration',()=>{
 const g=new OutageGuard();for(let t=0;t<=10000;t+=1000)g.evaluate(sample(500,t),t,true);
 assert(g.alerted);g.evaluate(sample(500,11000),11000,null);assert(g.alerted);
 for(let t=12000;t<=132000;t+=1000)g.evaluate(sample(0,t),t,true);
 assert(!g.alerted);g.evaluate(sample(500,133000),133000,false);assert(!g.alerted);
});
test('time weighted windows stable across sampling rates',()=>{
 for(const step of [1000,5000,10000]){const w=new Window(240000);for(let t=0;t<=240000;t+=step)w.add(t,t<120000?100:900);
 const f=w.stats(240000);assert.equal(f.mean,500);assert.equal(f.coverage,1);}
});
test('window gaps reduce coverage instead of fabricating history',()=>{
 const w=new Window(240000);w.add(0,800);w.add(240000,800);assert(w.stats(240000).coverage<0.1);
});
test('window buffer bounded in time',()=>{
 const w=new Window();for(let t=0;t<=7200000;t+=1000)w.add(t,100);assert(w.rows.length<=241);
});
test('rich dishwasher features use thirty seconds for fast running and four minutes warmup',()=>{
 for(const step of [1000,5000,10000]){const d=new DishwasherFeatures();let x;for(let t=0;t<=30000;t+=step)x=d.observe(sample(800,t),t);assert.equal(x.proposed,'running');assert.equal(x.quality,'warming');}
});
test('command gate drops 3599 duplicate attempts after successful ack',()=>{
 const g=new CommandGate();let sent=0;for(let t=0;t<3600000;t+=1000){const c=g.offer({state:'active',charge_current:12},t);if(c){sent++;g.ack(c.id,true,t);}}assert.equal(sent,1);
});
test('latest target wins without replaying a queued intermediate target',()=>{
 const g=new CommandGate();let c=g.offer({amps:6},0);g.ack(c.id,true,0);
 assert.equal(g.offer({amps:10},1000),null);c=g.offer({amps:16},10000);assert.equal(c.target.amps,16);
});
test('failure does not latch success and retry is bounded',()=>{
 const g=new CommandGate();const c=g.offer({amps:6},0);g.ack(c.id,false,1000);
 assert.equal(g.offer({amps:6},5000),null);assert(g.offer({amps:6},11000));
});
test('urgent stop bypasses normal cooldown and failed normal-command backoff',()=>{
 const g=new CommandGate();const c=g.offer({amps:6},0);g.ack(c.id,false,1);
 const stop=g.offer({state:'disabled'},2,{urgent:true});assert(stop);g.ack(stop.id,false,3);
 assert.equal(g.offer({state:'disabled'},4,{urgent:true}),null);
});
test('late response cannot acknowledge newer urgent command',()=>{
 const g=new CommandGate();const a=g.offer({amps:6},0);const b=g.offer({state:'disabled'},1,{urgent:true});
 assert(b);assert.equal(g.ack(a.id,true,2),false);assert(g.ack(b.id,true,3));
});
test('actuator state mismatch explicitly invalidates dedup',()=>{
 const g=new CommandGate();const a=g.offer({amps:6},0);g.ack(a.id,true,0);g.invalidate();assert(g.offer({amps:6},10000));
});
test('EV tuple includes outdoor loads and subtracts EV once',()=>{
 const p={mainsA:sample(2000,0),mainsB:sample(1500,0),ev:sample(1440,0),solar:sample(6000,0),outdoor:sample(500,0)};
 assert.deepEqual(evPowerTuple(p,0),{ok:true,totalHome:4000,nonEv:2560,availableWatts:3440});
 assert(!evPowerTuple({...p,outdoor:sample(500,-6000)},0).ok);assert(!evPowerTuple({...p,ev:sample(5000,0)},0).ok);
});
test('EV preserves automation-disable yield, but outage overrides it',()=>{
 assert(evIntent({gridOutage:false,automationDisabled:true}).yield);
 assert.deepEqual(evIntent({gridOutage:true,automationDisabled:true}),{target:{state:'disabled'},urgent:true});
 assert(evIntent({gridOutage:null,policyAmps:32}).hold);
 assert(evIntent({gridOutage:false,policyAmps:6,powerValid:false,mode:'hold_min_runtime'}).hold);
});
test('invalid EV amp requests cannot escape',()=>{
 for(const amps of [null,NaN,1,5,6.5,33])assert(evIntent({gridOutage:false,policyAmps:amps,powerValid:true,mode:'solar'}).hold);
});
test('proposed stale-solar stop waits thirty seconds and never takes manual ownership',()=>{
 const {SolarStaleGuard}=require('./logic.cjs'),g=new SolarStaleGuard();
 const args={solarOwned:true,inputsValid:false,gridOutage:false,automationDisabled:false,manualIntent:false};
 assert(!g.evaluate({...args,now:0}).stop);assert(!g.evaluate({...args,now:29000}).stop);assert(g.evaluate({...args,now:30000}).stop);
 assert(!g.evaluate({...args,now:31000,automationDisabled:true}).stop);
 assert(!g.evaluate({...args,now:32000,manualIntent:true}).stop);
 assert(g.evaluate({...args,now:33000,automationDisabled:true,gridOutage:true}).stop);
});
