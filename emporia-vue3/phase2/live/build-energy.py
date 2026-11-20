exec(open('emporia-vue3/phase2/live/build.py').read().split('# Iota tab:')[0])
t=tabs['afea81d223c193eb'];b=by(t)
for id,target in [('effusage_get_panel','effusage_build_mqtt'),('epower_get_solar','epower_build_raw_insert')]:
 old=b[id];new=dict(id=id,z=t['id'],type='function',name='Fresh Vue / outdoor / Powerwall snapshot',func="const p=global.get('VueHomePower');if(!p?.energyValid||Date.now()-p.at>20000||!Number.isFinite(p.total))return null;Object.assign(msg,{panelW:p.total,panelUsageW:p.total,solarW:p.solarW,powerwallOutW:p.powerwallOutW,heatPumpW:p.heatPumpW,effectiveUsageW:p.total+Math.max(-p.powerwallOutW,0)});return msg;",outputs=1,timeout=0,noerr=0,initialize='',finalize='',libs=[],wires=[[target]],x=old['x'],y=old['y']);t['nodes'][t['nodes'].index(old)]=new
s=b['effusage_build_mqtt']['func']
s=s.replace("source_panel_entity: 'sensor.total_home_and_heat_pump'", "source_panel_entity: 'sensor.emporia_vue3_home_power_live'")
s=s.replace('return [[\n    { topic: configTopic, payload: JSON.stringify(configPayload), retain: true, qos: 0 },\n    { topic: stateTopic, payload: JSON.stringify(statePayload), retain: true, qos: 0 }\n]];', '''const messages=[];
const homeTopic='homeassistant/sensor/vue3_phase2/home_power_live';
if(!globalThis._discovered){globalThis._discovered=true;
 messages.push({topic:configTopic,payload:JSON.stringify(configPayload),retain:true,qos:0});
 messages.push({topic:homeTopic+'/config',payload:JSON.stringify({name:'Home Power Live',unique_id:'vue3_phase2_home_power_live',default_entity_id:'sensor.emporia_vue3_home_power_live',state_topic:homeTopic+'/state',unit_of_measurement:'W',device_class:'power',state_class:'measurement',expire_after:45,device:{identifiers:['vue3_phase2'],name:'Emporia Vue3'}}),retain:true,qos:0});
}
messages.push({topic:stateTopic,payload:JSON.stringify(statePayload),retain:true,qos:0});
messages.push({topic:homeTopic+'/state',payload:String(Math.round(panelW)),retain:false,qos:0});
return [messages];''')
assert 'globalThis._discovered' in s
b['effusage_build_mqtt']['func']=s
p=work/(t['id']+'.candidate.json');p.write_text(json.dumps(t,indent=2));p.chmod(0o600)
print('Prepared Energy candidate: fresh total, existing 15s SQL cadence, retained discovery once per deploy.')
