'use strict';
// OFFLINE REFERENCE MODEL. No network, HA, Node-RED, filesystem or device side effects.
// Caller supplies monotonic milliseconds and converts source report times to that clock.
const finite = x => typeof x === 'number' && Number.isFinite(x);
const valid = (s, now, age=20000) => !!s && s.available === true && finite(s.watts) && s.watts >= 0 && finite(s.reportedAt) && s.reportedAt <= now && now-s.reportedAt < age;
class PowerCache {
  constructor(){ this.values=new Map(); }
  put(key,s,now){
    if(!s || !finite(s.reportedAt) || s.reportedAt>now) return false;
    const old=this.values.get(key);
    if(old && s.reportedAt<old.reportedAt) return false;
    // Explicit unavailable invalidates even if timestamp has not advanced.
    this.values.set(key,{...s});return true;
  }
  get(key){return this.values.get(key);}
}
class Appliance {
  constructor(kind,initial={}) {
    if(!['range','dryer','dishwasher'].includes(kind)) throw Error('unknown appliance');
    this.kind=kind;this.state={...initial};this.timers={};this.healthy=false;
    this.lastEval=null;this.lastReport=null;this.runningObservedMs=0;
  }
  dwell(key,condition,now){
    if(!condition){delete this.timers[key];return 0;}
    if(this.timers[key]===undefined)this.timers[key]=now;
    return now-this.timers[key];
  }
  evaluate(s,now){
    const out=[];
    const set=(key,value)=>{if(this.state[key]!==value){this.state[key]=value;out.push({kind:'transition',appliance:this.kind,key,value,at:now});}};
    const ok=valid(s,now);
    const discontinuity=this.lastReport!==null && s && s.reportedAt-this.lastReport>=20000;
    if(!ok || discontinuity || (this.lastEval!==null && (now<this.lastEval || now-this.lastEval>=20000))){this.timers={};this.healthy=false;}
    if(!ok){this.lastEval=now;return [{kind:'health',appliance:this.kind,available:false,at:now}];}
    // Accumulate qualified runtime only over known continuous observation.
    if(this.healthy && this.state.status==='running')this.runningObservedMs+=Math.max(0,now-this.lastEval);
    this.healthy=true;this.lastEval=now;this.lastReport=s.reportedAt;
    const w=s.watts;
    if(this.kind==='range'){
      const low=this.dwell('low',w<500,now);
      if(w>=500)set('range',true);
      else if(low>=60000)set('range',false);
      const heat=this.dwell('heat',w>100,now);
      // Preserve separate Range and OvenOn semantics and their off thresholds.
      if(heat>=180000)set('ovenOn',true);
      else if(low>=300000)set('ovenOn',false);
      if(this.state.range===true || this.state.ovenOn===true)set('indicator',true);
      else if(this.state.range===false && this.state.ovenOn!==true)set('indicator',false);
    } else if(this.kind==='dryer'){
      const high=this.dwell('high',w>=50,now),low=this.dwell('low',w<50,now);
      if(high>=60000 && this.state.status!=='running'){set('status','running');this.runningObservedMs=0;}
      if(low>=35000 && this.state.status==='running')set('status','complete');
    } else {
      // Legacy deadband: 10..40 W holds qualification; opposite threshold cancels.
      if(w>40){if(this.timers.high===undefined)this.timers.high=now;delete this.timers.low;}
      if(w<10){if(this.timers.low===undefined)this.timers.low=now;delete this.timers.high;}
      if(this.timers.high!==undefined && now-this.timers.high>=30000 && this.state.status!=='running'){
        set('status','running');this.runningObservedMs=0;
      }
      if(this.timers.low!==undefined && now-this.timers.low>=180000 && this.state.status==='running')
        set('status',this.runningObservedMs>=600000?'clean':'dirty');
    }
    return out;
  }
}
class OutageGuard {
  constructor(){this.high=null;this.low=null;this.alerted=false;this.lastReport=null;}
  evaluate(s,now,gridOutage){
    if(gridOutage===false){this.high=null;this.low=null;this.alerted=false;return [];}
    if(gridOutage!==true || !valid(s,now)){this.high=null;this.low=null;return [];}
    if(this.lastReport!==null && s.reportedAt-this.lastReport>=20000){this.high=null;this.low=null;}
    this.lastReport=s.reportedAt;
    if(s.watts>=100){this.low=null;if(this.high===null)this.high=now;}
    else {this.high=null;if(s.watts<20){if(this.low===null)this.low=now;if(now-this.low>=120000)this.alerted=false;}else this.low=null;}
    if(!this.alerted && this.high!==null && now-this.high>=10000){this.alerted=true;return [{kind:'dryer_outage_alert',at:now}];}
    return [];
  }
}
// Piecewise-constant, time-weighted features. Real reports, not fabricated duplicates.
class Window {
  constructor(ms=240000,maxGap=20000){this.ms=ms;this.maxGap=maxGap;this.rows=[];}
  add(t,w){if(!finite(t)||!finite(w)||w<0)return false;const last=this.rows.at(-1);if(last&&t<=last.t)return false;
    this.rows.push({t,w});while(this.rows.length>1&&this.rows[1].t<=t-this.ms)this.rows.shift();return true;}
  stats(now){let area=0,covered=0,min=Infinity,max=-Infinity;
    for(let i=0;i<this.rows.length;i++){const x=this.rows[i],next=this.rows[i+1];
      const a=Math.max(x.t,now-this.ms),b=Math.min(next?next.t:now,x.t+this.maxGap,now);
      if(b>a){area+=x.w*(b-a);covered+=b-a;min=Math.min(min,x.w);max=Math.max(max,x.w);}}
    return {mean:covered?area/covered:null,min:covered?min:null,max:covered?max:null,amp:covered?max-min:null,coverage:covered/this.ms};}
}
// Proposed rich dishwasher classifier remains diagnostic/shadow; no clean/dirty writer.
class DishwasherFeatures {
  constructor(){this.window=new Window();this.lastHi=null;this.aboveSince=null;this.last=null;}
  observe(s,now){
    if(!valid(s,now)){this.aboveSince=null;this.window=new Window();this.last=null;this.lastHi=null;return {quality:'unavailable'};}
    if(this.last!==null&&s.reportedAt-this.last>=20000){this.window=new Window();this.aboveSince=null;this.lastHi=null;}
    if(this.last===null||s.reportedAt>this.last){this.window.add(s.reportedAt,s.watts);this.last=s.reportedAt;
      if(s.watts>=650)this.lastHi=s.reportedAt;
      if(s.watts>100){if(this.aboveSince===null)this.aboveSince=s.reportedAt;}else this.aboveSince=null;}
    const f=this.window.stats(now),recent=this.lastHi!==null&&now-this.lastHi<=7200000;
    const fast=this.aboveSince!==null&&now-this.aboveSince>=30000;
    if(f.coverage<0.95)return {...f,quality:'warming',proposed:fast?'running':null};
    const clean=f.mean<=25;
    const running=!clean&&((f.max>=650&&f.amp<=120)||f.mean>=600);
    const drying=!clean&&!running&&recent&&now-this.lastHi>=600000&&f.max<=600&&f.amp>=180&&f.mean>65;
    return {...f,quality:'ready',proposed:clean?'clean':drying?'drying':'running',fastRunning:fast};
  }
}
// Transition intent gate. Successful acknowledgements, not attempts, establish dedup.
// No queue: caller re-evaluates newest desired state before each offer.
class CommandGate {
  constructor({minInterval=10000,retryDelay=10000,timeout=10000,keepalive=null}={}){
    this.minInterval=minInterval;this.retryDelay=retryDelay;this.timeout=timeout;this.keepalive=keepalive;
    this.lastKey=null;this.lastAck=-Infinity;this.lastAttempt=-Infinity;this.retryAt=-Infinity;this.pending=null;this.seq=0;
  }
  offer(target,now,{urgent=false,allowed=true}={}){
    if(!allowed)return null;
    const key=JSON.stringify(target);
    if(this.pending&&now-this.pending.at>=this.timeout){this.pending=null;this.retryAt=now+this.retryDelay;}
    if(this.pending){
      if(!(urgent&&this.pending.key!==key&&!this.pending.urgent))return null;
      // Urgent stop supersedes an in-flight normal command logically. Dispatcher
      // must track the old response and reconcile stopped state after it settles.
      this.pending=null;
    }
    if(key===this.lastKey&&(this.keepalive===null||now-this.lastAck<this.keepalive))return null;
    if(now<this.retryAt && !(urgent && key!==this.lastAttemptKey))return null;
    if(!urgent&&now-this.lastAttempt<this.minInterval)return null;
    const command={id:++this.seq,target,key,at:now,urgent};this.pending=command;this.lastAttempt=now;this.lastAttemptKey=key;return command;
  }
  ack(id,success,now){if(!this.pending||this.pending.id!==id)return false;
    if(success){this.lastKey=this.pending.key;this.lastAck=now;this.retryAt=-Infinity;}
    else this.retryAt=now+this.retryDelay;
    this.pending=null;return true;}
  // Use after actuator reconnect/mismatch. Retains rate bound, permits reconciliation.
  invalidate(){this.lastKey=null;}
}
// Build a coherent candidate EV power tuple before invoking preserved charging policy.
// Samples are aligned window averages supplied by adapter, not HA template last_updated.
function evPowerTuple({mainsA,mainsB,ev,solar,outdoor},now){
  const xs=[mainsA,mainsB,ev,solar,outdoor];
  if(!xs.every(x=>valid(x,now)))return {ok:false,reason:'missing/stale power source'};
  if(Math.max(...xs.map(x=>x.reportedAt))-Math.min(...xs.map(x=>x.reportedAt))>5000)return {ok:false,reason:'source skew'};
  const home=mainsA.watts+mainsB.watts+outdoor.watts;
  if(ev.watts>home+100)return {ok:false,reason:'EV exceeds measured home load'};
  return {ok:true,totalHome:home,nonEv:Math.max(0,home-ev.watts),availableWatts:solar.watts-Math.max(0,home-ev.watts)};
}
function evIntent({gridOutage,automationDisabled,policyAmps,powerValid,mode}){
  if(gridOutage===true)return {target:{state:'disabled'},urgent:true};
  // Preserve manual takeover: disable automation means yield, not force stop.
  if(automationDisabled===true)return {yield:true};
  if(gridOutage!==false)return {hold:true,reason:'grid status unknown'};
  if(['solar','solar_hold_min','hold_min_runtime'].includes(mode)&&!powerValid)return {hold:true,reason:'solar telemetry invalid'};
  if(!finite(policyAmps)||policyAmps<0||policyAmps>32||!Number.isInteger(policyAmps)||(policyAmps>0&&policyAmps<6))return {hold:true,reason:'invalid policy amps'};
  return {target:policyAmps?{state:'active',charge_current:policyAmps,auto_release:true}:{state:'disabled'},urgent:false};
}
module.exports={valid,PowerCache,Appliance,OutageGuard,Window,DishwasherFeatures,CommandGate,evPowerTuple,evIntent};
// Proposed explicit stale-solar policy, separate from the preserved EV policy.
// Session ownership must come from acknowledged actuator state, not proposed amps.
class SolarStaleGuard {
  constructor(){this.since=null;}
  evaluate({now,solarOwned,inputsValid,gridOutage,automationDisabled,manualIntent}){
    if(gridOutage===true){this.since=null;return {stop:true,urgent:true,reason:'grid outage'};}
    if(automationDisabled===true||manualIntent===true||!solarOwned){this.since=null;return {stop:false};}
    if(inputsValid){this.since=null;return {stop:false};}
    if(this.since===null)this.since=now;
    return {stop:now-this.since>=30000,urgent:false,reason:'solar telemetry stale',hold:true};
  }
}
module.exports.SolarStaleGuard=SolarStaleGuard;
