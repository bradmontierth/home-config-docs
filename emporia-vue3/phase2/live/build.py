import json,pathlib,copy,re
work=pathlib.Path('/tmp/vue-phase2-workdir').read_text();work=pathlib.Path(work)
root=pathlib.Path('emporia-vue3/phase2/live');base=pathlib.Path('emporia-vue3/phase2/logic.cjs').read_text();base='\n'.join(l for l in base.splitlines() if not l.startswith('module.exports'))
tabs={i:json.load(open(work/(i+'.before.json'))) for i in ['8123861fc119b96b','0711e9167e62e249','ee0f1588760b9b3b','7fa25727b15db1f0','afea81d223c193eb']}
allnodes=json.load(open(work/'flows-before.json'));byn={n['id']:n for n in allnodes};SERVER='23fd91e9137b71c5'
def node(tab,id,type,name,wires,**kw):
 d=dict(id=id,z=tab['id'],type=type,name=name,x=450,y=2200+len(tab['nodes'])*3,wires=wires,**kw);tab['nodes'].append(d);return d
def fn(tab,id,name,func,wires):return node(tab,id,'function',name,wires,func=func,outputs=len(wires),timeout=0,noerr=0,initialize='',finalize='',libs=[])
def inj(tab,id,name,repeat,to,once=False):return node(tab,id,'inject',name,[[to]],props=[{'p':'payload'},{'p':'topic','vt':'str'}],repeat=str(repeat),crontab='',once=once,onceDelay=10,topic='tick',payload='',payloadType='date')
def by(tab):return {n['id']:n for n in tab['nodes']}
def gate(tab,prefix,actions,acks):
 # (node ID, semantic target key); all paths into these actions get one gate.
 table=[{'id':a,'key':k} for a,k in actions];pre={a:prefix+'p'+str(i) for i,(a,k) in enumerate(actions)}
 for n in tab['nodes']:
  n['wires']=[[pre.get(target,target) for target in wire] for wire in n.get('wires',[])]
 for a,k in actions:fn(tab,pre[a],'Gate intent '+a,f'msg.vueRoute="{a}"; return msg;',[[prefix+'gate']])
 fn(tab,prefix+'gate','Acknowledged action gate','const ACTIONS='+json.dumps(table)+';\n'+(root/'action-gate.js').read_text(),[[a] for a,k in actions])
 inj(tab,prefix+'tick','Retry pending actions (5s)',5,prefix+'gate')
 fn(tab,prefix+'ack','Action result','msg.vueAck=true; return msg;',[[prefix+'gate']])
 b=by(tab)
 for a in acks:
  if not b[a].get('wires'):b[a]['wires']=[[]]
  b[a]['wires'][0].append(prefix+'ack')
 node(tab,prefix+'catch','catch','Action failures',[[prefix+'ack']],scope=acks,uncaught=False)
# Iota tab: retain separate outdoor IotaSolar ingestion, detach main Iota polling.
t=tabs['8123861fc119b96b'];b=by(t)
b['2481552dfbd0a209']['wires']=[['e6c1ad14ab5e3d15']]
b['25f87fb94d765e9b']['wires']=[[]]
b['031caeda013785ee']['wires']=[[]]
for id in ['e150d598f34fd51f','12057a28cc4a9f11']:b[id]['wires']=[[] for _ in b[id]['wires']]
# Main-Iota watchdog no longer power-cycles retired metering.
b['61fc241af08b3bb6']['func']='// Main IotaWatt CTs retired; no automatic recovery actuation.\nreturn null;'
# Outdoor input and old history remain. Alert checks require a fresh new EV reading.
b['8cb25de86d2bc003']['func']='const health=global.get("VuePowerHealth"); if(!health || Date.now()-health.at>20000 || !health.ev) return null;\n'+b['8cb25de86d2bc003']['func']
core=fn(t,'vue2core','Vue live power and elapsed-time decisions',base+'\n'+(root/'aligned-frame.js').read_text()+'\n'+(root/'core.js').read_text(),[['e24e7ffc0549b4ad','f86bd51f2936c3a6'],['c4fede30c4a21c09','1f1211f8f1ee5ec9'],['48cca974a2626b6a'],['375b2343b573e62b'],['vue2evout'],[],['vue2statusreply'],[]])
inj(t,'vue2tick','Vue deadline clock (local only)',1,'vue2core')
sources=json.load(open('emporia-vue3/phase2/migration-plan.json'))['sources'];entities=list(dict.fromkeys(sources.values()))+['sensor.tesla_powerwall_3_pw3_total_power_out','sensor.powerwalls_average','binary_sensor.powerwall_grid_outage','input_boolean.tesla_charge_asap','input_boolean.tesla_charge_tonight','input_boolean.tesla_charge_automation_disable','binary_sensor.dryer_contact_contact','input_select.dishwasher_status','input_select.dishwasher_new','input_boolean.oven_status']
event=copy.deepcopy(byn['d787a8977b828bb0']);event.update(id='vue2events',z=t['id'],name='Vue power and control events',entities={'entity':entities,'substring':[],'regex':[]},stateType='str',wires=[['vue2core']],x=130,y=2100);t['nodes'].append(event)
template="{% set ns=namespace(rows=[]) %}{% for id in "+json.dumps(entities)+" %}{% set s=states[id] %}{% if s is not none %}{% set ns.rows=ns.rows+[{'entity_id':id,'state':s.state,'last_reported':s.last_reported.isoformat()}] %}{% endif %}{% endfor %}{{ ns.rows|to_json }}"
node(t,'vue2snapshot','ha-api','Reconcile selected source reports', [['vue2core']],server=SERVER,version=1,debugenabled=False,protocol='http',method='post',path='/template',data=json.dumps({'template':template}),dataType='jsonata',responseType='json',outputProperties=[{'property':'payload','propertyType':'msg','value':'','valueType':'results'}])
inj(t,'vue2snapshotclock','Freshness snapshot (5s)',5,'vue2snapshot',True)
by(t)['vue2snapshotclock']['topic']='snapshot'
node(t,'vue2status','http in','Vue phase-two status', [['vue2core']],url='/vue3-power/status',method='get',upload=False,swaggerDoc='')
node(t,'vue2statusreply','http response','Read-only status',[],statusCode='',headers={'content-type':'application/json'})
node(t,'vue2evout','link out','Vue coherent EV inputs',[],mode='link',links=['vue2evin'])
gate(t,'vue2range',[('e24e7ffc0549b4ad','wled'),('c4fede30c4a21c09','wled'),('f86bd51f2936c3a6','oven-helper'),('1f1211f8f1ee5ec9','oven-helper')],['389d7ce92c196e68','e570555864ed3be1','f86bd51f2936c3a6','1f1211f8f1ee5ec9'])
# Dryer transitions reuse existing helper polarity, recent-door guard and notifications.
t=tabs['0711e9167e62e249'];b=by(t);b['533102b1284e532f']['wires']=[['vue2dryerroute']]
fn(t,'vue2dryerroute','Vue dryer transitions','if(msg.vueOutage)return [null,null,msg]; if(msg.vueStatus==="running")return [msg,null,null]; if(msg.vueStatus==="complete")return [null,msg,null]; return null;',[['0885f45788ddca2f'],['5a21c765034cab37'],['dryer_outage_detector']])
old=b['dryer_outage_detector']['func'];tail=old[old.index('const title ='):];tail=tail.replace('requiredHighSamples: REQUIRED_HIGH_SAMPLES','requiredHighSeconds: 10')
b['dryer_outage_detector']['func']='if(!msg.vueOutage)return null; const now=Date.now(),watts=Number(msg.payload.watts),gridOutage=true,gridOutageRaw=true,HIGH_WATTS=100;\n'+tail
b['dryer_outage_detector']['name']='Vue sustained dryer load during outage'
gate(t,'vue2dryer',[('0885f45788ddca2f','dryer-helper'),('a81c7829369775f2','dryer-helper')],['0885f45788ddca2f','a81c7829369775f2'])
# Dishwasher legacy transitions and wall-time drying classifier both live, separate helpers.
t=tabs['ee0f1588760b9b3b'];b=by(t);b['b470598e126b7d11']['wires']=[['vue2dishroute']]
fn(t,'vue2dishroute','Vue dishwasher state routing','if(msg.vueFeatures)return [null,null,null,msg];if(msg.vueStatus==="running")return [msg,null,null,null];if(msg.vueStatus==="clean")return [null,msg,null,null];if(msg.vueStatus==="dirty")return [null,null,msg,null];return null;',[['174d9986acd49cd3'],['be08e2b7890ab46a'],['6116721e4633d770'],['29bbcc343e24ac09']])
b['29bbcc343e24ac09']['func']=base+'\n'+(root/'dishwasher-rich.js').read_text();b['29bbcc343e24ac09']['name']='Vue dishwasher elapsed-time classifier'
b['174d9986acd49cd3']['wires']=[[]] # run-enough now counts observed healthy runtime
acts=[]
for n in t['nodes']:
 if n['type']=='api-call-service' and any(e in ['input_select.dishwasher_status','input_select.dishwasher_new'] for e in n.get('entityId',[])):
  acts.append((n['id'],n['entityId'][0]))
gate(t,'vue2dish',acts,[a for a,k in acts])
# EV and Energy tabs assembled by the next builder stage.
for i,t in tabs.items():
 p=work/(i+'.candidate.json');p.write_text(json.dumps(t,indent=2));p.chmod(0o600)
(root/'source-entities.json').write_text(json.dumps(entities,indent=2)+'\n')
print('Prepared range/dryer/dishwasher live tab candidates. No deploy yet.')
