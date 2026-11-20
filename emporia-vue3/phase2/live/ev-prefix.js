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
