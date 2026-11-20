exec(open('emporia-vue3/phase2/live/build.py').read().split('# Iota tab:')[0])
t=tabs['7fa25727b15db1f0'];b=by(t)
# No old Iota/global fanout can invoke combiner after selecting Vue.
b['42b634721e8471ed']['wires']=[[]];b['b626c434ae47ef94']['wires']=[[]]
node(t,'vue2evin','link in','Vue coherent EV inputs',[['b0b4f535c19d2ed7']],links=['vue2evout'])
s=b['b0b4f535c19d2ed7']['func']
s=s.replace('    const n = Number(v);\n    return Number.isFinite(n) ? n : null;','    if(v === null || v === undefined || v === "" || typeof v === "boolean") return null;\n    const n = Number(v);\n    return Number.isFinite(n) ? n : null;')
for prop in ['solarPW','pw','evChargingPower','totalHome']:
 s=s.replace(f' ?? cachedInputs.{prop} ?? null','')
s=s.replace('const telemetryReady = [solarPW, pw, evChargingPower, totalHome].every(Number.isFinite);','const telemetryReady = msg.vueTuple?.ok === true && [solarPW, pw, evChargingPower, totalHome].every(Number.isFinite);')
s=s.replace('state.recoveryActive = !!state.recoveryActive;',"const appliedState=flow.get('vueEvApplied'); if(appliedState){state.charging=appliedState.amps>0;state.chargingSince=appliedState.since;}\nstate.recoveryActive = !!state.recoveryActive;")
s=s.replace('return null;\n}', 'return [{vueYield:true},null];\n}',1) if False else s
# Explicit automation-disabled branch, leave other returns unchanged.
s=s.replace('context.set("evChargeState", state);\n    return null;', 'context.set("evChargeState", state);\n    return [{vueYield:true},null];')
# Do not turn missing vehicle telemetry into zero SOC / unsolicited recovery.
for car in ['Tesla_3_Data','Tesla_Y_Data']:
 s=s.replace(f'num(getState("{car}.battery_level", 0))',f'finiteOrNull(getState("{car}.battery_level", null))')
 s=s.replace(f'num(getState("{car}.charge_limit_soc", 80))',f'finiteOrNull(getState("{car}.charge_limit_soc", null))')
# Recovery reserve latch is committed by ACK; reserve gate suppresses pending duplicates.
s=s.replace('state.powerwallReserveSetForRecovery = {','state.powerwallReservePendingForRecovery = {')
s=s.replace('state.powerwallReserveSkippedForRecovery = null;\n} else {', 'state.powerwallReserveSkippedForRecovery = null;\n    state.powerwallReservePendingForRecovery=null;state.vueRecoveryCycle=null;\n} else {\n    if(!state.vueRecoveryCycle)state.vueRecoveryCycle=now.getTime();')
s=s.replace('topic: "powerwall_recovery_reserve",','topic: "powerwall_recovery_reserve",\n            vueRecoveryCycle:state.vueRecoveryCycle,')

b['b0b4f535c19d2ed7']['func']=(root/'ev-prefix.js').read_text()+'\n'+s
b['b0b4f535c19d2ed7']['name']='Vue charging policy (existing priorities)'
b['b0b4f535c19d2ed7']['wires']=[['vue2evgate'],['vue2reservegate']]
fn(t,'vue2evgate','EV changed-target / ACK / readback gate',(root/'ev-gate.js').read_text(),[['d8423915df88b573'],['vue2evreadhttp'],['vue2evstatusreply']])
inj(t,'vue2evgatetick','EV pending command clock',1,'vue2evgate')
b['d8423915df88b573']['wires']=[['vue2evack']]
fn(t,'vue2evack','EV command result','msg.vueEvAck=true; return msg;',[['vue2evgate']])
http=copy.deepcopy(b['d8423915df88b573']);http.update(id='vue2evreadhttp',name='OpenEVSE read-only reconciliation',wires=[['vue2evgate']]);t['nodes'].append(http)
node(t,'vue2evcatch','catch','EV request errors',[['vue2evack']],scope=['d8423915df88b573'],uncaught=False)
node(t,'vue2evreadcatch','catch','EV readback errors',[['vue2evgate']],scope=['vue2evreadhttp'],uncaught=False)
node(t,'vue2evstatus','http in','EV phase-two status',[['vue2evgate']],url='/vue3-ev/status',method='get',upload=False,swaggerDoc='')
node(t,'vue2evstatusreply','http response','EV read-only status',[],statusCode='',headers={'content-type':'application/json'})
# Low-SOC reserve command dedup: acknowledged latch is delivered back to policy context.
fn(t,'vue2reservegate','Recovery reserve acknowledged gate',"""const now=Date.now(); if(!globalThis._r)globalThis._r={last:null,pending:null,retry:0};const r=globalThis._r;
if(msg.vueReserveAck){if(r.pending===msg.vueReserveKey){if(!msg.error&&msg.statusCode>=200&&msg.statusCode<300){r.last=r.pending;flow.set('vueReserveAck',{key:r.last,at:now});}r.pending=null;r.retry=now+10000;}return null;}
if(r.pending&&now-r.sentAt>6000){r.pending=null;r.retry=now+10000;}
const key=JSON.stringify([msg.vueRecoveryCycle,msg.payload]);if(key===r.last||r.pending||now<r.retry)return null;
r.pending=key;r.sentAt=now;msg.vueReserveKey=key;msg.requestTimeout=5000;return msg;""",[['pw_reserve_recovery_http']])
fn(t,'vue2reserveack','Recovery reserve result','msg.vueReserveAck=true; return msg;',[['vue2reservegate']])
b['pw_reserve_recovery_http']['wires'][0].append('vue2reserveack')
node(t,'vue2reservecatch','catch','Reserve request errors',[['vue2reserveack']],scope=['pw_reserve_recovery_http'],uncaught=False)
# Commit only matching successful reserve ACK into policy's state.
b['b0b4f535c19d2ed7']['func']=b['b0b4f535c19d2ed7']['func'].replace('state.recoveryActive = !!state.recoveryActive;',"const reserveAck=flow.get('vueReserveAck'); if(reserveAck && state.powerwallReservePendingForRecovery && reserveAck.at>=Date.parse(state.powerwallReservePendingForRecovery.at)){state.powerwallReserveSetForRecovery=state.powerwallReservePendingForRecovery;}\nstate.recoveryActive = !!state.recoveryActive;")
p=work/(t['id']+'.candidate.json');p.write_text(json.dumps(t,indent=2));p.chmod(0o600)
print('Prepared EV candidate; charger v5.0.0 has session-based auto release, no periodic lease keepalive.')
