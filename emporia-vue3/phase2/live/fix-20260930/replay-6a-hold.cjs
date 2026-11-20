// Replays the Tesla-tab policy function around a single stale-telemetry tick
// during a running solar session (2026-09-30 13:43 incident).
// Usage: node replay-6a-hold.cjs policy-before.js|policy-after.js
const fs=require('fs'),vm=require('vm');
const body=fs.readFileSync(process.argv[2],'utf8');
const store={node:{},flow:{},global:{
 Tesla_3_Data:{plugged_in:'false',geofence:'Home',battery_level:'40',charge_limit_soc:'90'},
 Tesla_Y_Data:{plugged_in:'true',geofence:'Home',battery_level:'57',charge_limit_soc:'80'}}};
const ctx=o=>({get:k=>k.split('.').reduce((a,p)=>a==null?a:a[p],o),set:(k,v)=>{o[k]=v;}});
let t=Date.parse('2026-09-30T13:40:00-06:00');
const RealDate=Date;
class FakeDate extends RealDate{constructor(...a){super(...(a.length?a:[t]));}static now(){return t;}}
const sandbox={context:ctx(store.node),flow:ctx(store.flow),global:ctx(store.global),node:{warn(){},status(){}},Date:FakeDate,globalThis:{}};
sandbox.globalThis=sandbox;vm.createContext(sandbox);
const fn=new vm.Script('(function(msg){'+body+'\n})');const run=fn.runInContext(sandbox);
const ov={'input_boolean.tesla_charge_automation_disable':'off','input_boolean.tesla_charge_asap':'off','input_boolean.tesla_charge_tonight':'off'};
function tick(ok,amps){
 const ev=amps*240,m={vueInputs:true,vueAt:t,gridOutage:false,evChargeOverrides:ov,pw:+(process.env.PW||95.9),
  vueTuple:{ok},solarPW:ok?6000:null,iotawatt_ev_charging_power:ok?ev:null,total_home_and_heat_pump:ok?2000+ev:null};
 const out=run(m);const o=out&&out[0];
 if(o&&o.payload&&!o.vueHold){const a=o.payload.state==='active'?o.payload.charge_current:0;
  const prev=store.flow.vueEvApplied;store.flow.vueEvApplied={amps:a,since:a?(prev?.amps>0?prev.since:t):null,at:t,mode:o.debug.result.mode};return a;}
 return 'hold';
}
// Established 16A solar session.
store.flow.vueEvApplied={amps:16,since:t-600000,at:t,mode:'solar'};
store.node.evChargeState={charging:true,chargingSince:t-600000,solarEligibleSince:t-700000,lastAmps:16,lastMode:'solar'};
let amps=16;const log=[];
for(let i=0;i<40;i++){const ok=i!==+(process.env.STALE??5);const r=tick(ok,amps);if(r!=='hold')amps=r;log.push(`${i*5}s:${ok?'':'STALE→'}${r}`);t+=5000;}
console.log(log.join(' '));
const low=log.filter(x=>/:6$/.test(x)).length;
console.log(low?`FAIL: ${low} ticks at 6A after one stale tick`:'PASS: surplus tracked through stale tick');
process.exit(low?1:0);
