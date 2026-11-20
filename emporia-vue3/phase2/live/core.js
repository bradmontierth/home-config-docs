// Body appended to tested reference classes. Private runtime is memory-only.
const now=Date.now();
if(!globalThis._vue){globalThis._vue={cache:{},history:{},range:new Appliance('range',{range:!!global.get('Range'),ovenOn:!!global.get('OvenOn'),indicator:!!global.get('RangeLights')}),dryer:new Appliance('dryer'),dish:new Appliance('dishwasher'),outage:new OutageGuard(),lastSlow:0,lastEv:0,transitions:[],started:now};}
const r=globalThis._vue;
const names={range:'sensor.emporia_vue3_range_oven_power_live',dryer:'sensor.emporia_vue3_dryer_power_live',dish:'sensor.emporia_vue3_dishwasher_power_live',ev:'sensor.emporia_vue3_ev_charger_power_live',ac:'sensor.emporia_vue3_ac_power_live',furnace:'sensor.emporia_vue3_furnace_power_live',a:'sensor.emporia_vue3_mains_a_power_live',b:'sensor.emporia_vue3_mains_b_power_live',outdoor:'sensor.iotawattsolar_total_heat_pump_power',solar:'sensor.tesla_powerwall_3_pw3_total_solar',pwout:'sensor.tesla_powerwall_3_pw3_total_power_out'};
let rows=[];
if(Array.isArray(msg.payload))rows=msg.payload;
else if(typeof msg.payload==='string'&&msg.topic==='snapshot'){try{rows=JSON.parse(msg.payload);}catch(e){node.warn('Vue snapshot parse failed');}}
const event=msg.data?.new_state||msg.data?.event?.new_state;
if(event)rows.push(event);
let urgent=false;
for(const row of rows){if(!row?.entity_id)continue;const old=r.cache[row.entity_id],ts=Date.parse(row.last_reported||row.last_updated||'');if(!Number.isFinite(ts)||ts>now+2000||(old&&ts<old.ts))continue;
 r.cache[row.entity_id]={value:row.state,ts};
 const historyKey={a:'mainsA',b:'mainsB',ev:'ev',solar:'solar',outdoor:'outdoor'}[Object.keys(names).find(k=>names[k]===row.entity_id)];
 if(historyKey){const value=Number(row.state);let h=r.history[historyKey]||(r.history[historyKey]=[]);
 if(row.state===''||!Number.isFinite(value)||value<0){r.history[historyKey]=[];}
 else if(!h.length||ts>h[h.length-1].t){h.push({t:ts,w:value});while(h.length>1&&h[1].t<now-60000)h.shift();}}

 if(event===row&&(row.entity_id.startsWith('input_boolean.tesla_charge_')||row.entity_id==='binary_sensor.powerwall_grid_outage'))urgent=true;
 if(event===row&&row.entity_id==='binary_sensor.dryer_contact_contact'&&row.state==='on'){r.dryer.state.status='idle';r.dryer.timers={};}
 if(row.entity_id==='input_select.dishwasher_status'&&['clean','dirty'].includes(row.state)&&old&&old.value!==row.state){r.dish.state.status=row.state;r.dish.runningObservedMs=0;r.dish.timers={};}
}
const read=id=>r.cache[id];
const sample=key=>{const x=read(names[key]);const v=x&&x.value!==''&&!['unknown','unavailable'].includes(x.value)?Number(x.value):NaN;return {watts:v,reportedAt:x?.ts,available:Number.isFinite(v)&&v>=0};};
const outputs=Array.from({length:8},()=>[]);
const push=(channel,m)=>outputs[channel].push(m);
const record=e=>{r.transitions.push(e);if(r.transitions.length>80)r.transitions.shift();};
if(msg.req){msg.payload={started:r.started,now,health:Object.fromEntries(Object.keys(names).map(k=>[k,{...sample(k),fresh:valid(sample(k),now)}])),evTuple:r.lastTuple,history:Object.fromEntries(Object.entries(r.history).map(([k,h])=>[k,{count:h.length,last:h.at(-1)?.t,maxGapMs:Math.max(0,...h.slice(1).map((x,i)=>x.t-h[i].t))}])),states:{range:r.range.state,dryer:r.dryer.state,dishwasher:r.dish.state},transitions:r.transitions};push(6,msg);return outputs.map(x=>x.length?x:null);}
const range=r.range.evaluate(sample('range'),now);
for(const e of range){if(e.kind!=='transition')continue;record(e);if(e.key==='range')global.set('Range',e.value);if(e.key==='ovenOn')global.set('OvenOn',e.value);if(e.key==='indicator'){global.set('RangeLights',e.value);push(e.value?0:1,{payload:{},vueAction:e.value?'on':'off',vueAt:now});}}
for(const e of r.dryer.evaluate(sample('dryer'),now)){if(e.kind==='transition'){record(e);global.set('DryerIotaSwitch',e.value==='running');if(e.value==='running'||(e.value==='complete'&&global.get('dryerRecent')!==true))push(2,{payload:{},vueStatus:e.value,vueAt:now});}}
const grid=read('binary_sensor.powerwall_grid_outage');const gridKnown=grid&&now-grid.ts<30000;const gridValue=gridKnown?(grid.value==='on'?true:grid.value==='off'?false:null):null;
for(const e of r.outage.evaluate(sample('dryer'),now,gridValue)){record(e);push(2,{payload:{watts:sample('dryer').watts},vueOutage:true,vueAt:now,gridOutage:true});}
if(!r.dishInitial&&read('input_select.dishwasher_status')){r.dish.state.status=read('input_select.dishwasher_status').value;r.dishInitial=true;}
for(const e of r.dish.evaluate(sample('dish'),now)){if(e.kind==='transition'){record(e);push(3,{payload:{},vueStatus:e.value,vueAt:now});}}
if(now-r.lastSlow>=5000){r.lastSlow=now;
 const runEnough=r.dish.runningObservedMs>=600000;if(r.runEnough!==runEnough){r.runEnough=runEnough;global.set('dishwasherRunEnough',runEnough);}
 for(const [key,g] of [['ev','Tesla'],['ac','AC'],['furnace','Furnace'],['dish','dishwasherPower']])global.set(g,valid(sample(key),now)?sample(key).watts:null);
 global.set('VuePowerHealth',{at:now,ev:valid(sample('ev'),now),range:valid(sample('range'),now),dryer:valid(sample('dryer'),now),dishwasher:valid(sample('dish'),now)});
 push(3,{vueFeatures:true,vueSample:sample('dish'),vueCurrentNew:read('input_select.dishwasher_new')?.value,vueAt:now});
 const a=sample('a'),b=sample('b'),o=sample('outdoor');const good=[a,b,o].every(s=>valid(s,now));
 const total=good?a.watts+b.watts+o.watts:null;
 const po=read(names.pwout),poW=po&&po.value!==''?Number(po.value):NaN;
 global.set('VueHomePower',{at:now,valid:good,indoor:good?a.watts+b.watts:null,total,sourceTimes:[a.reportedAt,b.reportedAt,o.reportedAt],heatPumpW:o.watts,solarW:sample('solar').watts,powerwallOutW:poW,energyValid:good&&valid(sample('solar'),now)&&Number.isFinite(poW)&&now-po.ts<30000});
}
if(urgent||now-r.lastEv>=5000){r.lastEv=now;
 const tuple=alignedFrame(r,{mainsA:sample('a'),mainsB:sample('b'),ev:sample('ev'),solar:sample('solar'),outdoor:sample('outdoor')},now);r.lastTuple=tuple;
 const overrides={};for(const suffix of ['asap','tonight','automation_disable']){const id='input_boolean.tesla_charge_'+suffix;overrides[id]=read(id)?.value;}
 const pw=read('sensor.powerwalls_average');
 push(4,{vueInputs:true,vueAt:now,vueTuple:tuple,vuePower:Object.fromEntries(Object.keys(names).map(k=>[k,sample(k)])),gridOutage:gridValue===null?'unknown':gridValue,evChargeOverrides:overrides,pw:pw&&!['unknown','unavailable'].includes(pw.value)?Number(pw.value):null,solarPW:tuple.ok?tuple.samples.solar.watts:null,iotawatt_ev_charging_power:tuple.ok?tuple.samples.ev.watts:null,total_home_and_heat_pump:tuple.ok?tuple.totalHome:null,vueUrgent:urgent});
}
node.status({fill:valid(sample('range'),now)&&valid(sample('dryer'),now)&&valid(sample('dish'),now)?'green':'yellow',shape:'dot',text:'Vue live | '+(r.dryer.state.status||'?')+' / '+(r.dish.state.status||'?')});
return outputs.map(x=>x.length?x:null);
